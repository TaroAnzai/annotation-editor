from __future__ import annotations
import hashlib, json, os, re, shutil, tempfile, time
from pathlib import Path
from PIL import Image
import yaml
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / 'data'
EDITOR = ROOT / 'annotation-editor'
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}

def safe(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(DATA.resolve()):
        raise HTTPException(403, 'data外へのアクセスは禁止です')
    return resolved

def ident(path: Path) -> str:
    return hashlib.sha256(str(path.relative_to(DATA)).encode()).hexdigest()[:24]

def natural(s: str):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)', s)]

def folders():
    def node(path):
        children = []
        for p in sorted(path.iterdir(), key=lambda x: natural(x.name)):
            if p.is_dir() and not p.is_symlink():
                children.append(node(p))
        return {'id': ident(path), 'name': path.name, 'path': str(path.relative_to(DATA)), 'children': children}
    return node(DATA)

def image_paths(path):
    return sorted((p for p in path.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in IMAGE_EXT and safe(p)), key=lambda p: natural(p.name))

def datasets():
    out = []
    for root, dirs, files in os.walk(DATA, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(root)/d).is_symlink() and 'preview' not in d.lower()]
        p = Path(root)
        if p == DATA or 'preview' in p.name.lower(): continue
        imgs = p/'images'
        labels = p/'labels'
        yml = p/'data.yaml'
        if imgs.is_dir() and labels.is_dir() and yml.is_file() and not any(x.is_symlink() for x in (imgs,labels,yml)):
            out.append({'id': ident(p), 'name': p.parent.name if p.name == 'roboflow_import' else p.name, 'path': str(p.relative_to(DATA)), 'images': len(image_paths(imgs)), 'editable': True})
            dirs[:] = [d for d in dirs if d not in ('images','labels')]
        elif yml.is_file() and not yml.is_symlink():
            splits=[]
            for split in ('train','val','valid','test'):
                q=p/split
                if (q/'images').is_dir() and (q/'labels').is_dir() and not (q/'images').is_symlink() and not (q/'labels').is_symlink():
                    splits.append(split)
                    out.append({'id':ident(q),'name':p.name+' / '+split,'path':str(q.relative_to(DATA)),'images':len(image_paths(q/'images')),'editable':True,'yaml_path':str(yml.relative_to(DATA))})
            dirs[:] = [d for d in dirs if d not in splits]
        elif image_paths(p) and not ((p/'roboflow_import'/'images').is_dir() and (p/'roboflow_import'/'labels').is_dir() and (p/'roboflow_import'/'data.yaml').is_file()):
            out.append({'id': ident(p), 'name': p.name, 'path': str(p.relative_to(DATA)), 'images': len(image_paths(p)), 'editable': False})
    return sorted(out, key=lambda d: natural(d['path']))

def dataset(dataset_id):
    for d in datasets():
        if d['id'] == dataset_id:
            p = safe(DATA/d['path'])
            return d, p
    raise HTTPException(404, 'データセットが見つかりません')

def classes(p):
    try:
        y = yaml.safe_load((p/'data.yaml').read_text(encoding='utf-8'))
        names = y['names']
        if isinstance(names, dict): names = [names[i] if i in names else names[str(i)] for i in range(len(names))]
        if not isinstance(names, list) or not names or not all(isinstance(x,str) and x for x in names) or y.get('nc',len(names)) != len(names): raise ValueError('names/ncが不正です')
        return names
    except Exception as e:
        raise HTTPException(422, f'data.yaml読み込みエラー: {e}')

def classes_for(d,p):
    return classes(safe(DATA/d['yaml_path']).parent) if d.get('yaml_path') else classes(p)

def images(dataset_id):
    d,p = dataset(dataset_id)
    folder = p/'images' if d['editable'] else p
    result=[]
    nclasses=len(classes_for(d,p)) if d['editable'] else 0
    for f in image_paths(folder):
        label=p/'labels'/f'{f.stem}.txt'
        count=0; error=False
        if d['editable'] and label.exists():
            try: count=len(parse_label(label.read_text(encoding='utf-8-sig'),nclasses))
            except (UnicodeError,HTTPException): error=True
        state=status_for(f)
        result.append({'id':ident(f),'name':f.name,'status':state['status'],'modified':state['modified'],'label_exists':label.exists() if d['editable'] else False,'bbox_count':count,'label_error':error})
    return result

def image(image_id):
    for d in datasets():
        p = safe(DATA/d['path'])
        folder = p/'images' if d['editable'] else p
        for f in image_paths(folder):
            if ident(f)==image_id: return d,p,f
    raise HTTPException(404, '画像が見つかりません')

def state_path(f):
    return EDITOR/'state'/(ident(f)+'.json')

def status_for(f):
    try: return json.loads(state_path(f).read_text(encoding='utf-8'))
    except FileNotFoundError: return {'status':'unreviewed','modified':False}

def atomic_write(path, content: bytes):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.annotation-', delete=False) as h:
            tmp = Path(h.name); h.write(content); h.flush(); os.fsync(h.fileno())
        os.replace(tmp,path)
    finally:
        if tmp and tmp.exists(): tmp.unlink()

def set_status(f, status, modified=None):
    if status not in ('unreviewed','reviewed','held'): raise HTTPException(422,'状態が不正です')
    state=status_for(f); state['status']=status
    if modified is not None: state['modified']=modified
    atomic_write(state_path(f),json.dumps(state,ensure_ascii=False).encode())
    return state

def version(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else 'missing'

def parse_label(raw, nclasses):
    rows=[]
    for line_num,line in enumerate(raw.splitlines(),1):
        if not line.strip(): continue
        parts=line.split()
        try:
            if len(parts)!=5: raise ValueError('5列ではありません')
            cid=int(parts[0]); vals=[float(x) for x in parts[1:]]
            if str(cid)!=parts[0] or not 0<=cid<nclasses: raise ValueError('クラスIDが不正です')
            x,y,w,h=vals
            if not all(0<=v<=1 for v in vals) or w<=0 or h<=0 or x-w/2 < -1e-8 or y-h/2 < -1e-8 or x+w/2 > 1+1e-8 or y+h/2 > 1+1e-8: raise ValueError('BBox座標が不正です')
            rows.append({'class_id':cid,'x':x-w/2,'y':y-h/2,'w':w,'h':h})
        except Exception as e: raise HTTPException(422,f'ラベル {line_num}行目: {e}')
    return rows

def validate_boxes(boxes,nclasses):
    if not isinstance(boxes,list): raise HTTPException(422,'BBox一覧が不正です')
    for b in boxes:
        try:
            cid=b['class_id']; x,y,w,h=[float(b[k]) for k in ('x','y','w','h')]
            if type(cid)!=int or not 0<=cid<nclasses or not all(__import__('math').isfinite(v) for v in (x,y,w,h)) or x<0 or y<0 or w<=0 or h<=0 or x+w>1+1e-9 or y+h>1+1e-9: raise ValueError()
        except Exception: raise HTTPException(422,'BBoxまたはクラスIDが不正です')

def label_info(d,p,f):
    if not d['editable']: return {'boxes':[],'version':'readonly','error':None}
    names=classes_for(d,p); path=safe(p/'labels'/f'{f.stem}.txt')
    v=version(path)
    try: boxes=parse_label(path.read_text(encoding='utf-8-sig') if path.exists() else '',len(names)); err=None
    except (UnicodeError,HTTPException) as e: boxes=[];err=str(e.detail if isinstance(e,HTTPException) else e)
    return {'boxes':boxes,'version':v,'error':err}

def save(d,p,f,boxes,expected):
    if not d['editable']: raise HTTPException(403,'閲覧専用です')
    names=classes_for(d,p);validate_boxes(boxes,len(names))
    path=safe(p/'labels'/f'{f.stem}.txt')
    if version(path)!=expected: raise HTTPException(409,'外部でラベルが変更されました。再読み込みしてください')
    old=label_info(d,p,f)
    if old['error']: raise HTTPException(422,'元ラベルにエラーがあります。保存を中止しました')
    newline='\r\n' if path.exists() and b'\r\n' in path.read_bytes() else '\n'
    lines=[f"{b['class_id']} {b['x']+b['w']/2:.10f} {b['y']+b['h']/2:.10f} {b['w']:.10f} {b['h']:.10f}" for b in boxes]
    content=((newline.join(lines)+newline) if lines else '').encode()
    if path.exists() and path.read_bytes()==content: return old
    if path.exists():
        backup=EDITOR/'backups'/path.relative_to(DATA)
        backup=backup.with_name(backup.name+f'.{time.time_ns()}.bak')
        backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,backup)
    # Validate serialized text before replacing the source.
    parse_label(content.decode(),len(names))
    atomic_write(path,content)
    set_status(f,status_for(f)['status'],True)
    return label_info(d,p,f)
