"""Read-only import inspection and explicit transactional registration."""
from __future__ import annotations
import json, shutil, tempfile, os, uuid
from pathlib import Path
from PIL import Image
import yaml
from . import core, registry as reg
from .schema import TILE_CLASSES, validate_coco

class ImportErrorDetail(ValueError): pass

def source_path(value:str,mode:str):
    original=Path(value).expanduser()
    if original.is_symlink(): raise ImportErrorDetail('シンボリックリンクの元フォルダーは取り込めません')
    p=original.resolve()
    if not p.is_dir(): raise ImportErrorDetail(f'フォルダーがありません: {p}')
    if mode=='register' and (not p.is_relative_to((core.DATA/'sources').resolve()) or p==(core.DATA/'sources').resolve()): raise ImportErrorDetail('登録はsources直下のフォルダーだけです')
    if mode!='register' and p.is_relative_to(core.DATA.resolve()): raise ImportErrorDetail('DATA_ROOT内は登録モードを使用してください')
    for item in p.rglob('*'):
        if item.is_symlink(): raise ImportErrorDetail(f'シンボリックリンクは取り込めません: {item}')
    return p

def mapping(root:Path, names:list[str]):
    config=root/'dataset.yaml'; data=yaml.safe_load(config.read_text()) if config.exists() else {}
    explicit=data.get('class_mapping') if isinstance(data,dict) else None
    if explicit is not None:
        if set(explicit)!=set(names) or set(explicit.values())!=set(TILE_CLASSES) or len(explicit)!=37: raise ImportErrorDetail('class_mappingは37クラスを一対一で指定してください')
        return {i:TILE_CLASSES.index(explicit[name]) for i,name in enumerate(names)}
    aliases={'5mr':'red5m','5pr':'red5p','5sr':'red5s'}
    normalized=[aliases.get(n,n) for n in names]
    if len(normalized)!=37 or set(normalized)!=set(TILE_CLASSES): raise ImportErrorDetail('37クラスの明示的なmappingが必要です')
    return {i:TILE_CLASSES.index(n) for i,n in enumerate(normalized)}

def yolo_config(root):
    p=root/'data.yaml'
    if not p.exists(): return None
    value=yaml.safe_load(p.read_text(encoding='utf-8'))
    names=value.get('names')
    if isinstance(names,dict): names=[names.get(i,names.get(str(i))) for i in range(len(names))]
    if not isinstance(names,list) or value.get('nc',len(names))!=len(names) or not all(isinstance(x,str) for x in names): raise ImportErrorDetail('data.yamlのクラス定義が不正です')
    return mapping(root,names)

def layout(root:Path):
    if all((root/s/'images').is_dir() for s in ('train','val','test')): return 'dataset'
    if (root/'images').is_dir() and (root/'annotations'/'instances.json').is_file(): return 'merged' if (root/'manifest.json').exists() and json.loads((root/'manifest.json').read_text()).get('dataset_type')=='merged' else 'prepared' if (root/'manifest.json').exists() else 'coco'
    if (root/'data.yaml').exists(): return 'yolo'
    if any(root.rglob('_annotations.coco.json')): return 'roboflow_coco'
    if any(root.rglob('*.coco.json')): return 'coco'
    return 'images'

def pieces(root:Path,kind:str):
    if kind=='dataset':
        for split in ('train','val','test'):
            p=root/split; label=next((x for x in (p/'labels'/'instances.json',p/'annotations'/'instances.json') if x.exists()),None)
            if not label: raise ImportErrorDetail(f'{split}のCOCOラベルがありません')
            yield p,split,'coco',label
    elif kind in ('prepared','merged','coco') and (root/'annotations'/'instances.json').exists(): yield root,'pool','coco',root/'annotations'/'instances.json'
    elif kind=='yolo':
        if (root/'images').is_dir(): yield root,'pool','yolo',None
        else:
            for split in ('train','valid','val','test'):
                p=root/split
                if (p/'images').is_dir(): yield p,split,'yolo',None
    elif kind=='roboflow_coco':
        for split in ('train','valid','val','test'):
            p=root/split
            if p.is_dir() and (p/'_annotations.coco.json').exists(): yield p,split,'coco',p/'_annotations.coco.json'
    else: yield root,'pool','images',None

def inspect(root:Path,source_type:str|None=None):
    kind=layout(root); rows=[]; problems=[]; image_hashes={}; anns={}; boxes_total=0
    top_map=yolo_config(root) if kind=='yolo' else None
    type_map=json.loads((root/'source_types.json').read_text(encoding='utf-8')) if (root/'source_types.json').exists() else {}
    for base,split,fmt,annotation_file in pieces(root,kind):
        imgdir=base/'images' if (base/'images').is_dir() else base
        files=sorted(p for p in imgdir.rglob('*') if p.suffix.lower() in core.IMAGE_EXT and p.is_file())
        coco=None; ann_by={}
        if fmt=='coco':
            coco=json.loads(annotation_file.read_text(encoding='utf-8'))
            validate_coco(coco,imgdir,False)
            names={int(c['id']):c['name'] for c in coco['categories']}
            if names=={i+1:n for i,n in enumerate(TILE_CLASSES)}: cmap={i:i-1 for i in names}
            else:
                config=root/'dataset.yaml'
                specified=(yaml.safe_load(config.read_text()) or {}).get('class_mapping') if config.exists() else None
                if not specified or set(specified.values())!=set(TILE_CLASSES) or set(specified)!=set(names.values()): raise ImportErrorDetail('COCOカテゴリには完全なclass_mappingが必要です')
                cmap={i:TILE_CLASSES.index(specified[n]) for i,n in names.items()}
            for a in coco['annotations']: ann_by.setdefault(a['image_id'],[]).append(a)
            records={str(i['file_name']):i for i in coco['images']}
            if len(records)!=len(coco['images']): raise ImportErrorDetail('COCO画像名が重複しています')
        else: records={}
        for f in files:
            try:
                with Image.open(f) as im: size=im.size; im.verify()
                h=reg.sha(f); typ=(records.get(str(f.relative_to(imgdir)),{}).get('source_type') or type_map.get(str(f.relative_to(root))) or source_type)
                if typ not in ('real','synthetic'): raise ImportErrorDetail(f'source_typeが必要です: {f}')
                boxes=[]; ann_path=None; image_key=None
                if fmt=='coco':
                    key=str(f.relative_to(imgdir)); record=records.get(key)
                    if record is None: raise ImportErrorDetail(f'COCOに画像がありません: {f}')
                    image_key=record['id'];ann_path=annotation_file
                    for a in ann_by.get(image_key,[]):
                        x,y,w,bh=a['bbox']; boxes.append({'class_id':cmap[a['category_id']],'x':x/size[0],'y':y/size[1],'w':w/size[0],'h':bh/size[1]})
                elif fmt in ('yolo','images'):
                    label=base/'labels'/f'{f.stem}.txt';ann_path=label if label.exists() else None
                    if ann_path:
                        mapping_ids=top_map or {i:i for i in range(37)}
                        for b in core.parse_label(label.read_text(encoding='utf-8-sig'),len(mapping_ids)):
                            b['class_id']=mapping_ids[b['class_id']];boxes.append(b)
                core.validate_boxes(boxes,37)
                d=reg.box_digest(boxes); boxes_total+=len(boxes)
                if h in image_hashes and image_hashes[h]!=d: problems.append(f'同じ画像でannotation競合: {f}')
                image_hashes[h]=d
                rows.append({'path':str(f),'rel':str(f.relative_to(root)),'split':split,'sha256':h,'width':size[0],'height':size[1],'source_type':typ,'boxes':boxes,'annotation_path':str(ann_path) if ann_path else None,'image_key':image_key,'format':fmt})
            except Exception as e: problems.append(f'{f}: {e}')
        if fmt=='coco' and len(records)!=len(files): problems.append(f'{annotation_file}: 画像とCOCO定義の件数が異なります')
    if kind in ('prepared','merged'):
        manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('image_count')!=len(rows) or manifest.get('annotation_count')!=boxes_total: problems.append('manifestの画像・BBox数が一致しません')
        summary={t:sum(r['source_type']==t for r in rows) for t in ('real','synthetic')}
        if manifest.get('source_type_summary') and manifest['source_type_summary']!=summary: problems.append('manifestのsource_type_summaryが一致しません')
        coco_value=json.loads((root/'annotations'/'instances.json').read_text(encoding='utf-8'))
        annotation_digest='sha256:'+reg.digest(coco_value)
        inventory_digest='sha256:'+reg.digest(sorted((str(Path(r['rel']).relative_to('images')),r['sha256']) for r in rows))
        if manifest.get('annotation_sha256') and manifest['annotation_sha256']!=annotation_digest: problems.append('manifestのannotation_sha256が一致しません')
        if manifest.get('image_inventory_sha256') and manifest['image_inventory_sha256']!=inventory_digest: problems.append('manifestのimage_inventory_sha256が一致しません')
        if manifest.get('manifest_version')==2 and any(k not in manifest for k in ('dataset_id','annotation_sha256','image_inventory_sha256','leaf_sources')): problems.append('manifest v2の必須項目がありません')
    elif kind=='dataset':
        manifest=json.loads((root/'dataset_manifest.json').read_text(encoding='utf-8')) if (root/'dataset_manifest.json').exists() else None
        if manifest is None: problems.append('dataset_manifest.jsonがありません')
        elif any(manifest.get('splits',{}).get(s,{}).get('image_count')!=sum(r['split']==s for r in rows) for s in ('train','val','test')): problems.append('dataset_manifestのsplit件数が一致しません')
        if manifest:
            for split in ('train','val','test'):
                label=next((p for p in (root/split/'labels'/'instances.json',root/split/'annotations'/'instances.json') if p.exists()),None)
                facts=manifest.get('splits',{}).get(split,{})
                if label and facts.get('annotation_sha256')!='sha256:'+reg.digest(json.loads(label.read_text())): problems.append(f'{split}のannotation manifestが一致しません')
                inventory='sha256:'+reg.digest(sorted((Path(r['rel']).name,r['sha256']) for r in rows if r['split']==split))
                if facts.get('image_inventory_sha256')!=inventory: problems.append(f'{split}の画像manifestが一致しません')
    else: manifest=None
    with reg.connect() as db:
        duplicates=sum(db.execute('SELECT 1 FROM images WHERE sha256=?',(r['sha256'],)).fetchone() is not None for r in rows)
        conflicts=[]
        for r in rows:
            existing_type=db.execute('SELECT source_type FROM images WHERE sha256=?',(r['sha256'],)).fetchone()
            if existing_type and existing_type[0]!=r['source_type']: problems.append(f"source_type競合: {r['rel']}")
            old=db.execute('SELECT a.content_sha256 FROM annotations a JOIN images i ON i.id=a.image_id WHERE i.sha256=?',(r['sha256'],)).fetchall()
            if any(x[0]!=reg.box_digest(r['boxes']) for x in old): conflicts.append(r['rel'])
    return {'kind':kind,'rows':rows,'errors':problems,'image_count':len(rows),'annotation_count':sum(bool(r['boxes']) for r in rows),'bbox_count':boxes_total,'duplicates':duplicates,'conflicts':conflicts,'manifest':manifest}

def preview(value:str,mode:str,source_type:str|None):
    root=source_path(value,mode); result=inspect(root,source_type)
    return {k:v for k,v in result.items() if k!='rows'} | {'preview_id':reg.digest([str(root),mode,source_type,[(r['rel'],r['sha256'],reg.box_digest(r['boxes'])) for r in result['rows']]])}

def register(value:str,mode:str,source_type:str|None,name:str,expected:str,origin:str='',progress=None):
    if mode not in ('copy','move','register'): raise ImportErrorDetail('取り込み方法が不正です')
    if not name or name in ('.','..') or Path(name).name!=name or '/' in name: raise ImportErrorDetail('バッチ名が不正です')
    root=source_path(value,mode); scan=inspect(root,source_type)
    if scan['errors'] or scan['conflicts']: raise ImportErrorDetail('取り込みエラーまたはannotation競合: '+str(scan['errors']+scan['conflicts']))
    token=reg.digest([str(root),mode,source_type,[(r['rel'],r['sha256'],reg.box_digest(r['boxes'])) for r in scan['rows']]])
    if token!=expected: raise ImportErrorDetail('プレビュー後に入力が変更されました')
    if progress: progress(1)
    if scan['kind'] in ('merged','dataset'): raise ImportErrorDetail('merged/datasetはバージョン取り込みを使用してください')
    destination=core.DATA/'sources'/name
    if mode=='register':
        if root!=destination.resolve(): raise ImportErrorDetail('バッチ名はsourcesのフォルダー名と一致させてください')
        staged=None
    else:
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists(): raise ImportErrorDetail('同名バッチは既に存在します')
        staged=Path(tempfile.mkdtemp(prefix='.import-',dir=core.DATA/'tmp' if (core.DATA/'tmp').exists() else destination.parent))
        try:
            shutil.copytree(root,staged/'payload')
            copied=inspect(staged/'payload',source_type)
            if [(r['rel'],r['sha256']) for r in copied['rows']]!=[(r['rel'],r['sha256']) for r in scan['rows']]: raise ImportErrorDetail('コピー検証に失敗しました')
            (staged/'payload').replace(destination)
        finally: shutil.rmtree(staged,ignore_errors=True)
    if progress: progress(2)
    try:
        with reg.connect() as db:
            existing=db.execute('SELECT id FROM batches WHERE name=? OR path=?',(name,reg.rel(destination))).fetchone()
            if existing: return {'batch_id':existing[0],'already_registered':True}
            cur=db.execute('INSERT INTO batches(name,path,format,origin,provenance,created_at) VALUES(?,?,?,?,?,?)',(name,reg.rel(destination),scan['kind'],origin,json.dumps(scan['manifest'] or {},ensure_ascii=False),reg.now()))
            batch_id=cur.lastrowid
            for r in scan['rows']:
                f=destination/r['rel'];h=reg.sha(f)
                if h!=r['sha256']: raise ImportErrorDetail(f'コピー検証失敗: {f}')
                old=db.execute('SELECT id FROM images WHERE sha256=?',(h,)).fetchone()
                if old: image_id=old[0]
                else:
                    image_id=db.execute('INSERT INTO images(sha256,path,filename,width,height,source_type,origin,created_at) VALUES(?,?,?,?,?,?,?,?)',(h,reg.rel(f),f.name,r['width'],r['height'],r['source_type'],origin,reg.now())).lastrowid
                if db.execute('SELECT 1 FROM batch_images WHERE batch_id=? AND image_id=?',(batch_id,image_id)).fetchone(): continue
                db.execute('INSERT INTO batch_images(batch_id,image_id,path,duplicate) VALUES(?,?,?,?)',(batch_id,image_id,reg.rel(f),bool(old)))
                d=reg.box_digest(r['boxes']); ann_path=reg.rel(destination/Path(r['annotation_path']).relative_to(root)) if r['annotation_path'] else None
                status=('provisional' if r['format']=='yolo' else 'unreviewed') if r['boxes'] else 'unannotated'
                ann_id=db.execute('INSERT INTO annotations(batch_id,image_id,format,path,image_key,content_sha256,content_json,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(batch_id,image_id,r['format'],ann_path,str(r['image_key']) if r['image_key'] is not None else None,d,json.dumps(r['boxes']),status,reg.now())).lastrowid
                db.execute('INSERT INTO annotation_history(annotation_id,revision,content_sha256,changed_at) VALUES(?,?,?,?)',(ann_id,1,d,reg.now()))
    except Exception:
        if mode!='register': shutil.rmtree(destination,ignore_errors=True)
        raise
    if mode=='move': shutil.rmtree(root)
    if progress: progress(3)
    return {'batch_id':batch_id,'already_registered':False,'image_count':len(scan['rows'])}
