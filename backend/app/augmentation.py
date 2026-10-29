"""Deterministic, staged source augmentation with canonical COCO output.

Pillow is already a project dependency (HPND license). Geometry uses the same
inverse affine mapping as Image.rotate, then clips the transformed rectangle.
"""
from __future__ import annotations
import hashlib, io, json, math, random, re, shutil, tempfile
from pathlib import Path
from PIL import Image, ImageEnhance, ImageDraw
from fastapi import HTTPException
from . import core, importer, registry
from .schema import CATEGORIES, TILE_CLASSES, validate_coco

DEFAULTS = {
    'brightness': {'enabled': True, 'min': .8, 'max': 1.2, 'probability': 1.0},
    'contrast': {'enabled': True, 'min': .9, 'max': 1.1, 'probability': 1.0},
    'rotation': {'enabled': True, 'min': -3.0, 'max': 3.0, 'probability': 1.0},
}

def settings(value):
    value=value or {}
    if not isinstance(value,dict) or set(value)-set(DEFAULTS): raise ValueError('Augmentation設定が不正です')
    result={}
    for key,default in DEFAULTS.items():
        if not isinstance(value.get(key,{}),dict): raise ValueError(f'{key}設定が不正です')
        entry={**default,**value.get(key,{})}
        if set(entry)!=set(default) or type(entry['enabled']) is not bool: raise ValueError(f'{key}設定が不正です')
        for field in ('min','max','probability'):
            if type(entry[field]) not in (int,float) or not math.isfinite(entry[field]): raise ValueError(f'{key}の{field}が不正です')
        if not 0<=entry['probability']<=1 or entry['min']>entry['max']: raise ValueError(f'{key}の範囲が不正です')
        if key in ('brightness','contrast') and (entry['min']<=0 or entry['max']>4): raise ValueError(f'{key}は0より大きく4以下にしてください')
        if key=='rotation' and (entry['min'] < -45 or entry['max'] > 45): raise ValueError('回転は±45度以内にしてください')
        result[key]=entry
    return result

def options(dataset_ids, name, variants, seed, transforms):
    if isinstance(dataset_ids,str): dataset_ids=[dataset_ids]
    if not isinstance(dataset_ids,list) or not dataset_ids or any(not isinstance(value,str) or not value for value in dataset_ids) or len(dataset_ids)!=len(set(dataset_ids)):
        raise ValueError('元データセットを一つ以上、重複なく選択してください')
    sources=[]
    for dataset_id in dataset_ids:
        d,p=core.dataset(dataset_id)
        if not p.is_relative_to(core.DATA/'sources') or p.parent!=core.DATA/'sources': raise ValueError('sources直下のデータセットを選択してください')
        if d.get('format')=='images': raise ValueError('アノテーションのないデータセットは選択できません')
        sources.append((d,p))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',name or ''): raise ValueError('出力名は英数字・_・- の64文字以内で指定してください')
    if type(variants) is not int or not 1<=variants<=20: raise ValueError('生成倍率は1〜20を指定してください')
    if type(seed) is not int: raise ValueError('seedは整数で指定してください')
    config=settings(transforms)
    if not any(v['enabled'] and v['probability']>0 for v in config.values()): raise ValueError('変換を一つ以上有効にしてください')
    return sorted(sources,key=lambda source:source[0]['path']),config

def selected_rows(sources,source_type):
    rows=[]
    for d,p in sources:
        for row in source_rows(d,p,source_type):
            rows.append({**row,'source_dataset':d['path']})
    return rows

def source_rows(d,p,source_type='real'):
    # inspect reads COCO/YOLO with the same canonical 0-based class mapping as import.
    with registry.connect() as db:
        batch=db.execute('SELECT id FROM batches WHERE path=?',(registry.rel(p),)).fetchone()
        registered_type=db.execute('SELECT i.source_type FROM batch_images bi JOIN images i ON i.id=bi.image_id WHERE bi.batch_id=? LIMIT 1',(batch['id'],)).fetchone()[0] if batch else None
    if source_type not in ('real','synthetic'): raise ValueError('画像種類が不正です')
    scan=importer.inspect(p,registered_type or source_type)
    if scan['errors']: raise ValueError('元データセットにエラーがあります: '+str(scan['errors']))
    rows=scan['rows']
    if not rows or any(not r['boxes'] for r in rows): raise ValueError('BBoxのない画像があります')
    lineage=p/'augmentation.json'
    if lineage.is_file():
        mapping={item['generated_image']:item['source_sha256'] for item in json.loads(lineage.read_text(encoding='utf-8')).get('images',[])}
        for row in rows: row['origin_sha256']=mapping.get(Path(row['path']).name,row['sha256'])
    return sorted(rows,key=lambda r:r['rel'])

def parameters(config,seed,source_sha,variant):
    rng=random.Random(f'{seed}:{source_sha}:{variant}')
    return {key:(rng.uniform(entry['min'],entry['max']) if entry['enabled'] and rng.random()<entry['probability'] else None) for key,entry in config.items()}

def rotate_box(box,w,h,degrees):
    x,y,bw,bh=(box['x']*w,box['y']*h,box['w']*w,box['h']*h)
    angle=math.radians(degrees); c=math.cos(angle); s=math.sin(angle)
    corners=[]
    for px,py in ((x,y),(x+bw,y),(x+bw,y+bh),(x,y+bh)):
        dx,dy=px-w/2,py-h/2
        corners.append((w/2+c*dx+s*dy,h/2-s*dx+c*dy))
    # Clip the rotated quadrilateral to the image so visibility uses actual area.
    polygon=corners
    for axis,bound,inside in ((0,0,True),(0,w,False),(1,0,True),(1,h,False)):
        clipped=[]
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            av,bv=a[axis],b[axis]
            ai=av>=bound if inside else av<=bound
            bi=bv>=bound if inside else bv<=bound
            if ai!=bi:
                t=(bound-av)/(bv-av)
                clipped.append((a[0]+t*(b[0]-a[0]),a[1]+t*(b[1]-a[1])))
            if bi: clipped.append(b)
        polygon=clipped
        if not polygon: return None,0,1
    area=lambda points: abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1]))/2)
    visible=area(polygon)/(bw*bh)
    xs=[point[0] for point in polygon];ys=[point[1] for point in polygon]
    left,top,right,bottom=max(0,min(xs)),max(0,min(ys)),min(w,max(xs)),min(h,max(ys))
    if right-left<2 or bottom-top<2 or visible<.5: return None,visible,1
    bbox={'class_id':box['class_id'],'x':left/w,'y':top/h,'w':(right-left)/w,'h':(bottom-top)/h}
    inflation=((right-left)*(bottom-top))/max(area(polygon),1e-9)
    return bbox,visible,inflation

def transform(image,boxes,params):
    im=image.convert('RGB')
    if params['brightness'] is not None: im=ImageEnhance.Brightness(im).enhance(params['brightness'])
    if params['contrast'] is not None: im=ImageEnhance.Contrast(im).enhance(params['contrast'])
    warnings=[]; out=[]
    angle=params['rotation']
    if angle is not None:
        im=im.rotate(angle,resample=Image.Resampling.BICUBIC,expand=False,fillcolor=(0,0,0))
        for index,box in enumerate(boxes):
            candidate,visible,inflation=rotate_box(box,*image.size,angle)
            if candidate is None:
                warnings.append(f'BBox {index+1}: 可視率 {visible:.2f} のため生成画像を不採用')
                return None,None,warnings
            if inflation>1.5: warnings.append(f'BBox {index+1}: 外接矩形の面積比 {inflation:.2f}')
            out.append(candidate)
    else: out=[dict(b) for b in boxes]
    core.validate_boxes(out,37)
    return im,out,warnings

def render_preview(dataset_ids,seed,transforms,index=0,variant=0,source_type='real'):
    sources,config=options(dataset_ids,'preview',1,seed,transforms)
    rows=selected_rows(sources,source_type)
    if type(index) is not int or not 0<=index<len(rows): raise ValueError('画像番号が不正です')
    row=rows[index]; params=parameters(config,seed,row['sha256'],variant)
    with Image.open(row['path']) as source:
        original=source.convert('RGB'); changed,boxes,warnings=transform(original,row['boxes'],params)
    if changed is None: raise ValueError('この設定では画像外へ出るBBoxがあります: '+' / '.join(warnings))
    def encoded(im,items):
        copy=im.copy();draw=ImageDraw.Draw(copy)
        for b in items:
            x,y,w,h=[b[k] for k in ('x','y','w','h')]
            draw.rectangle((x*im.width,y*im.height,(x+w)*im.width,(y+h)*im.height),outline='#ff3030',width=2)
            draw.text((x*im.width,y*im.height),TILE_CLASSES[b['class_id']],fill='#ffff00')
        data=io.BytesIO();copy.save(data,format='PNG');return data.getvalue()
    return {'original':encoded(original,row['boxes']),'augmented':encoded(changed,boxes),'params':params,'warnings':warnings,'image':f"{row['source_dataset']}/{Path(row['path']).name}",'index':index,'total':len(rows),'boxes':boxes}

def create(dataset_ids,name,variants=1,seed=42,transforms=None,progress=None,source_type='real'):
    sources,config=options(dataset_ids,name,variants,seed,transforms)
    target=core.DATA/'sources'/name
    if target.exists(): raise ValueError('同名の出力データセットがあります')
    rows=selected_rows(sources,source_type)
    signature=hashlib.sha256(json.dumps({'sources':[(r['source_dataset'],r['sha256'],r['rel']) for r in rows],'seed':seed,'variants':variants,'transforms':config},sort_keys=True).encode()).hexdigest()
    with registry.connect() as db:
        for batch in db.execute('SELECT provenance FROM batches'):
            try:
                if json.loads(batch['provenance']).get('signature')==signature: raise ValueError('同じ元画像・設定・seedのAugmentationデータセットは既に登録されています')
            except json.JSONDecodeError: pass
    if progress: progress(0,len(rows)*variants)
    target.parent.mkdir(parents=True,exist_ok=True)
    staged=Path(tempfile.mkdtemp(prefix='.augmentation-',dir=target.parent))
    coco={'images':[],'annotations':[],'categories':CATEGORIES}; records=[]; warnings=[]; processed=0; generated_hashes=set()
    try:
        (staged/'images').mkdir();(staged/'annotations').mkdir()
        for row in rows:
            with Image.open(row['path']) as source: original=source.convert('RGB')
            for variant in range(variants):
                params=parameters(config,seed,row['sha256'],variant)
                changed,boxes,issues=transform(original,row['boxes'],params)
                warnings.extend(f"{row['source_dataset']}/{row['rel']} #{variant+1}: {issue}" for issue in issues)
                if changed is None: raise ValueError('品質基準を満たさない画像があります: '+warnings[-1])
                name_out=f"{hashlib.sha256((row['source_dataset']+'/'+row['rel']).encode()).hexdigest()[:12]}-{row['sha256'][:12]}-aug{variant+1:02d}.png"
                changed.save(staged/'images'/name_out,format='PNG')
                image_hash=registry.sha(staged/'images'/name_out)
                if image_hash in generated_hashes: raise ValueError('同一内容の派生画像が複数生成されました')
                generated_hashes.add(image_hash)
                image_id=len(coco['images'])+1
                coco['images'].append({'id':image_id,'file_name':name_out,'width':changed.width,'height':changed.height,'source_type':row['source_type']})
                for b in boxes:
                    x,y,bw,bh=b['x']*changed.width,b['y']*changed.height,b['w']*changed.width,b['h']*changed.height
                    coco['annotations'].append({'id':len(coco['annotations'])+1,'image_id':image_id,'category_id':b['class_id']+1,'bbox':[x,y,bw,bh],'area':bw*bh,'iscrowd':0})
                records.append({'source_dataset':row['source_dataset'],'source_image':row['rel'],'source_sha256':row.get('origin_sha256',row['sha256']),'immediate_source_sha256':row['sha256'],'generated_image':name_out,'source_type':row['source_type'],'parameters':params})
                processed+=1
                if progress: progress(processed,len(rows)*variants)
        (staged/'annotations'/'instances.json').write_text(json.dumps(coco,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        manifest={'dataset_type':'augmentation','augmentation_version':1,'source_datasets':[d['path'] for d,_ in sources],'seed':seed,'variants_per_image':variants,'transforms':config,'signature':signature,'images':records,'warnings':warnings}
        (staged/'augmentation.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        validate_coco(coco,staged/'images')
        scan=importer.inspect(staged)
        issues=scan['errors']+scan['conflicts']
        with registry.connect() as db:
            augmented_hashes={record['sha256'] for record in db.execute('SELECT i.sha256,b.provenance FROM images i JOIN batch_images bi ON bi.image_id=i.id JOIN batches b ON b.id=bi.batch_id')
                              if json.loads(record['provenance']).get('dataset_type')=='augmentation'}
        if generated_hashes & augmented_hashes: issues.append('既存のAugmentation画像と同一内容の画像があります')
        if issues: raise ValueError('生成データセットの検証に失敗しました: '+str(issues))
        if target.exists(): raise ValueError('同名の出力データセットがあります')
        staged.replace(target)
        try:
            preview=importer.preview(str(target),'register',None)
            registered=importer.register(str(target),'register',None,name,preview['preview_id'],origin='augmentation:'+','.join(d['path'] for d,_ in sources))
            if registered['already_registered']: raise ValueError('同名の登録バッチがあります')
        except Exception:
            shutil.rmtree(target,ignore_errors=True)
            raise
        return {'name':name,'image_count':processed,'warning_count':len(warnings),'batch_id':registered['batch_id']}
    finally: shutil.rmtree(staged,ignore_errors=True)
