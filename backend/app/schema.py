"""Canonical tile schema and COCO editing. Names match mahjong-tile-ai labels.py (Apache-2.0)."""
from __future__ import annotations
import json, math, shutil, time
from pathlib import Path
from fastapi import HTTPException
import yaml

TILE_CLASSES = tuple(f'{rank}{suit}' for suit in 'mps' for rank in range(1, 10)) + ('east','south','west','north','white','green','red','red5m','red5p','red5s')
CATEGORIES = [{'id': i, 'name': name} for i,name in enumerate(TILE_CLASSES,1)]
def canonical_names(): return TILE_CLASSES

def category_mapping(path:Path,value):
    names={int(c['id']):c['name'] for c in value['categories']}
    if names=={c['id']:c['name'] for c in CATEGORIES}: return {i:i-1 for i in names}
    config=next((p for p in (path.parent/'dataset.yaml',path.parent.parent/'dataset.yaml',path.parent.parent.parent/'dataset.yaml') if p.is_file()),None)
    specified=(yaml.safe_load(config.read_text()) or {}).get('class_mapping') if config else None
    if not specified or set(specified)!=set(names.values()) or len(specified)!=37 or set(specified.values())!=set(TILE_CLASSES): raise HTTPException(422,'COCOクラスには37クラスの明示的なmappingが必要です')
    return {i:TILE_CLASSES.index(specified[n]) for i,n in names.items()}

def read_coco(path:Path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or any(not isinstance(value.get(k),list) for k in ('images','annotations','categories')): raise ValueError('COCO配列が不正です')
        return value
    except (OSError,ValueError) as e: raise HTTPException(422,f'COCO読み込みエラー: {e}')

def validate_coco(value, image_files=None, require_canonical=True):
    from PIL import Image
    if require_canonical and {int(c['id']):c['name'] for c in value['categories']} != {c['id']:c['name'] for c in CATEGORIES}: raise ValueError('canonical37カテゴリが一致しません')
    ids=[i['id'] for i in value['images']]; aids=[a['id'] for a in value['annotations']]
    if len(ids)!=len(set(ids)) or len(aids)!=len(set(aids)): raise ValueError('COCO IDが重複しています')
    indexed={i['id']:i for i in value['images']}; classes={c['id'] for c in value['categories']}
    for i in value['images']:
        name=Path(i['file_name'])
        if name.is_absolute() or '..' in name.parts: raise ValueError(f"危険な画像名: {name}")
        if image_files is not None:
            p=(image_files/name).resolve()
            if not p.is_relative_to(image_files.resolve()) or p.is_symlink() or not p.is_file(): raise ValueError(f'画像がありません: {name}')
            with Image.open(p) as im:
                if im.size != (i['width'],i['height']): raise ValueError(f'画像寸法が違います: {name}')
                im.verify()
    for a in value['annotations']:
        i=indexed.get(a['image_id']); x,y,w,h=a['bbox']
        if i is None or a['category_id'] not in classes or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (x,y,w,h)) or x<0 or y<0 or w<=0 or h<=0 or x+w>i['width']+1e-6 or y+h>i['height']+1e-6: raise ValueError(f"不正BBox: annotation {a.get('id')}")
    return value

def coco_boxes(path:Path, image:Path):
    value=validate_coco(read_coco(path),require_canonical=False)
    mapping=category_mapping(path,value)
    matches=[i for i in value['images'] if Path(i['file_name']).name==image.name]
    if len(matches)!=1: raise HTTPException(422,'COCO画像対応が一意ではありません')
    i=matches[0]
    return [{'class_id':mapping[a['category_id']],'x':a['bbox'][0]/i['width'],'y':a['bbox'][1]/i['height'],'w':a['bbox'][2]/i['width'],'h':a['bbox'][3]/i['height']} for a in value['annotations'] if a['image_id']==i['id']]

def save_coco(path:Path,image:Path,boxes,expected):
    from . import core
    core.validate_boxes(boxes,37)
    if core.version(path)!=expected: raise HTTPException(409,'COCOが外部で変更されました')
    value=validate_coco(read_coco(path),require_canonical=False)
    reverse={v:k for k,v in category_mapping(path,value).items()}
    matches=[i for i in value['images'] if Path(i['file_name']).name==image.name]
    if len(matches)!=1: raise HTTPException(422,'COCO画像対応が一意ではありません')
    record=matches[0]; old=[a for a in value['annotations'] if a['image_id']==record['id']]
    max_id=max((int(a['id']) for a in value['annotations']),default=0)
    new=[]
    for n,b in enumerate(boxes):
        w,h=record['width'],record['height']; box=[b['x']*w,b['y']*h,b['w']*w,b['h']*h]
        ann=dict(old[n]) if n<len(old) else {'id':max_id+n-len(old)+1,'image_id':record['id'],'iscrowd':0}
        ann.update(category_id=reverse[b['class_id']],bbox=box,area=box[2]*box[3]); new.append(ann)
    value['annotations']=[a for a in value['annotations'] if a['image_id']!=record['id']]+new
    raw=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
    if path.read_bytes()!=raw:
        backup=core.DATA/'backups'/path.relative_to(core.DATA)
        backup=backup.with_name(backup.name+f'.{time.time_ns()}.bak'); backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,backup)
        core.atomic_write(path,raw)
    return {'boxes':boxes,'version':core.version(path),'error':None}
