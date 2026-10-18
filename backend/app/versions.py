"""Immutable canonical COCO dataset versions and reproducible splits."""
from __future__ import annotations
import json, shutil, tempfile, random, hashlib, re
from pathlib import Path
from . import core, registry as reg, importer
from .schema import CATEGORIES, validate_coco, category_mapping, normalize_mapping_config

SPLITS=('train','val','test')
def jd(v): return 'sha256:'+reg.digest(v)
def write_json(p,v): p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def next_id(db):
    ids=[int(r[0][1:]) for r in db.execute("SELECT id FROM versions WHERE id GLOB 'v[0-9]*'") if re.fullmatch(r'v\d+',r[0])]
    return f'v{max(ids,default=0)+1:03d}'
def versions():
    with reg.connect() as db:
        result=[dict(r) for r in db.execute('SELECT id,parent_id,kind,description,created_at,test_id,status,upload_status,manifest_json FROM versions ORDER BY created_at DESC')]
        for version in result:
            version['included_batch_ids']=[r[0] for r in db.execute('''
                SELECT b.id FROM batches b
                WHERE EXISTS (SELECT 1 FROM batch_images bi WHERE bi.batch_id=b.id)
                  AND NOT EXISTS (
                    SELECT 1 FROM batch_images bi
                    WHERE bi.batch_id=b.id AND NOT EXISTS (
                      SELECT 1 FROM version_images vi
                      WHERE vi.version_id=? AND vi.image_id=bi.image_id
                    )
                  )
            ''',(version['id'],))]
        return result
def rows_from_db(batch_ids=None,image_ids=None,require_review=True):
    with reg.connect() as db:
        query='''SELECT i.id image_id,i.sha256,bi.path,i.filename,i.width,i.height,i.source_type,a.content_json,a.content_sha256,a.status,b.id batch_id,b.provenance FROM images i JOIN annotations a ON a.image_id=i.id JOIN batches b ON b.id=a.batch_id JOIN batch_images bi ON bi.batch_id=b.id AND bi.image_id=i.id'''
        rows=[dict(r) for r in db.execute(query)]
    selected=[]; seen={}
    for r in rows:
        if batch_ids is not None and r['batch_id'] not in batch_ids: continue
        if image_ids and r['image_id'] not in image_ids: continue
        if require_review and r['status']!='reviewed': continue
        key=r['image_id']
        if key in seen and seen[key]['content_sha256']!=r['content_sha256']: raise ValueError(f"annotation競合: {r['filename']}")
        seen[key]=r
    selected=list(seen.values())
    if selected:
        with reg.connect() as db:
            for batch_id in {r['batch_id'] for r in selected}:
                batch=db.execute('SELECT path FROM batches WHERE id=?',(batch_id,)).fetchone()
                current=importer.inspect(reg.path(batch[0]),next(r['source_type'] for r in selected if r['batch_id']==batch_id))
                errors=[e for e in current['errors'] if not e.startswith('manifestの')]
                if errors: raise ValueError('バッチのファイルが不整合です: '+str(errors))
                scanned={reg.rel(Path(r['path'])):r for r in current['rows']}
                for row in (r for r in selected if r['batch_id']==batch_id):
                    file=scanned.get(row['path'])
                    if not file or file['sha256']!=row['sha256'] or reg.box_digest(file['boxes'])!=row['content_sha256']:
                        raise ValueError(f"DBとファイルの内容が違います: {row['filename']}")
    return selected

def create_unpartitioned(batch_ids=None,description='',progress=None):
    """Freeze registered source annotations before TEST and split decisions."""
    selected=rows_from_db(batch_ids,require_review=False)
    if not selected: raise ValueError('対象画像がありません')
    if any(not json.loads(r['content_json']) for r in selected): raise ValueError('BBoxのない画像があります')
    if progress: progress(1)
    with reg.connect() as db:
        version_id=next_id(db)
        batch_info=[dict(r) for r in db.execute('SELECT id,name,origin FROM batches WHERE id IN ('+','.join('?' for _ in {r['batch_id'] for r in selected})+')',tuple({r['batch_id'] for r in selected}))]
    target=core.DATA/'versions'/version_id;target.parent.mkdir(parents=True,exist_ok=True)
    staged=Path(tempfile.mkdtemp(prefix='.unpartitioned-',dir=target.parent))
    try:
        coco=render_split(staged,'pool',selected)
        (staged/'pool'/'images').replace(staged/'images')
        (staged/'pool'/'labels').replace(staged/'annotations')
        (staged/'pool').rmdir()
        if progress: progress(2)
        image_inventory=sorted((p.name,reg.sha(p)) for p in (staged/'images').iterdir())
        manifest={'manifest_version':2,'dataset_type':'editor_unpartitioned','dataset_id':jd(sorted((r['sha256'],r['content_sha256']) for r in selected)),'created_at':reg.now(),'image_count':len(coco['images']),'annotation_count':len(coco['annotations']),'annotation_sha256':jd(coco),'image_inventory_sha256':jd(image_inventory),'source_type_summary':{t:sum(r['source_type']==t for r in selected) for t in ('real','synthetic')},'source_batches':batch_info,'review_status_summary':{s:sum(r['status']==s for r in selected) for s in ('reviewed','unreviewed','provisional','held','unannotated')},'description':description,'split_status':'unpartitioned'}
        write_json(staged/'manifest.json',manifest)
        staged.replace(target)
        try:
            with reg.connect() as db:
                db.execute('INSERT INTO versions(id,kind,path,description,created_at,manifest_json,dataset_hash,status) VALUES(?,?,?,?,?,?,?,?)',(version_id,'unpartitioned',reg.rel(target),description,reg.now(),json.dumps(manifest),manifest['dataset_id'],'unpartitioned'))
                for r in selected:
                    db.execute('INSERT INTO version_images(version_id,image_id,split,image_sha256,annotation_sha256,content_json,source_type) VALUES(?,?,?,?,?,?,?)',(version_id,r['image_id'],'pool',r['sha256'],r['content_sha256'],r['content_json'],r['source_type']))
        except Exception: shutil.rmtree(target);raise
        if progress: progress(3)
        return {'id':version_id,'image_count':len(selected),'manifest':manifest}
    finally: shutil.rmtree(staged,ignore_errors=True)
def parent_rows(db,parent):
    return [{**dict(r),'sha256':r['image_sha256'],'content_sha256':r['annotation_sha256']} for r in db.execute('SELECT vi.*,i.path,i.filename,i.width,i.height FROM version_images vi JOIN images i ON i.id=vi.image_id WHERE vi.version_id=?',(parent,))]
def split_rows(rows,parent_rows_,fixed,real_count,synthetic_count,val_ratio,seed):
    if not 0<=val_ratio<1: raise ValueError('Validation比率は0以上1未満です')
    combined={r['image_id']:r for r in parent_rows_}
    for r in rows:
        key=r['image_id']
        if key in combined: continue
        combined[key]=r
    all_rows=list(combined.values())
    if not all_rows: raise ValueError('確認済み画像がありません')
    if fixed:
        selected={r['image_id'] for r in parent_rows_ if r['split']=='test'}
        if not selected: raise ValueError('親バージョンのTESTがありません')
    else:
        selected=set()
        for typ,count in (('real',real_count),('synthetic',synthetic_count)):
            if type(count)!=int or count<0: raise ValueError('TEST枚数は0以上の整数です')
            available=sorted((r for r in all_rows if r['source_type']==typ),key=lambda r:r['sha256'])
            if count>len(available): raise ValueError(f'{typ} TEST不足: required={count} available={len(available)}')
            random.Random(f'{seed}:{typ}').shuffle(available)
            selected.update(r['image_id'] for r in available[:count])
    result=[]
    for r in all_rows:
        item=dict(r)
        if item['image_id'] in selected: item['split']='test'
        elif fixed and item['image_id'] in {x['image_id'] for x in parent_rows_}: item['split']=item.get('split','train') if item.get('split')!='test' else 'train'
        else:
            bucket=int(hashlib.sha256(f"{seed}:{item['sha256']}".encode()).hexdigest(),16)/(1<<256)
            item['split']='val' if bucket<val_ratio else 'train'
        result.append(item)
    if not any(r['split']=='train' for r in result) or (val_ratio>0 and not any(r['split']=='val' for r in result)): raise ValueError('train/valに十分な画像がありません')
    return result

def render_split(root:Path,split:str,rows):
    out=root/split; image_dir=out/'images';image_dir.mkdir(parents=True)
    coco={'images':[],'annotations':[],'categories':CATEGORIES}
    for n,r in enumerate(sorted(rows,key=lambda r:r['sha256']),1):
        source=reg.path(r['path'])
        if reg.sha(source)!=r['sha256']: raise ValueError(f'画像ハッシュが変わりました: {source}')
        name=f"{r['sha256'][:20]}{source.suffix.lower()}";shutil.copy2(source,image_dir/name)
        coco['images'].append({'id':n,'file_name':name,'width':r['width'],'height':r['height'],'source_type':r['source_type']})
        boxes=json.loads(r['content_json'])
        if reg.box_digest(boxes)!=r['content_sha256']: raise ValueError(f'annotationハッシュが変わりました: {source}')
        if not boxes: raise ValueError(f'BBoxがありません: {source}')
        for b in boxes:
            bbox=[b['x']*r['width'],b['y']*r['height'],b['w']*r['width'],b['h']*r['height']]
            coco['annotations'].append({'id':len(coco['annotations'])+1,'image_id':n,'category_id':b['class_id']+1,'bbox':bbox,'area':bbox[2]*bbox[3],'iscrowd':0})
    write_json(out/'labels'/'instances.json',coco)
    validate_coco(coco,image_dir)
    return coco

def facts(root,split,source_id,leaf_sources,preserved=False):
    coco=json.loads((root/split/'labels'/'instances.json').read_text())
    files=sorted((p.name,reg.sha(p)) for p in (root/split/'images').iterdir() if p.is_file())
    return {'source_dataset_id':source_id,'leaf_sources':leaf_sources,'preserved':preserved,'image_count':len(coco['images']),'annotation_count':len(coco['annotations']),'source_type_summary':{t:sum(i['source_type']==t for i in coco['images']) for t in ('synthetic','real')},'annotation_sha256':jd(coco),'image_inventory_sha256':jd(files)}
def validate(root:Path,allow_legacy=False):
    if (root/'manifest.json').is_file() and not (root/'dataset_manifest.json').exists():
        manifest=json.loads((root/'manifest.json').read_text())
        coco=json.loads((root/'annotations'/'instances.json').read_text())
        validate_coco(coco,root/'images',require_canonical=not allow_legacy)
        if allow_legacy: category_mapping(root/'annotations'/'instances.json',coco)
        inventory=sorted((p.name,reg.sha(p)) for p in (root/'images').iterdir() if p.is_file())
        if manifest.get('image_count')!=len(coco['images']) or manifest.get('annotation_count')!=len(coco['annotations']) or manifest.get('annotation_sha256')!=jd(coco) or manifest.get('image_inventory_sha256')!=jd(inventory): raise ValueError('未分割バージョンのmanifestが不一致です')
        return {'valid':True,'kind':'unpartitioned','count':len(coco['images']),'dataset_id':manifest['dataset_id']}
    manifest=json.loads((root/'dataset_manifest.json').read_text())
    found={}; hashes=set()
    for split in SPLITS:
        p=root/split/'labels'/'instances.json';coco=json.loads(p.read_text())
        validate_coco(coco,root/split/'images',require_canonical=not allow_legacy)
        if allow_legacy: category_mapping(p,coco)
        fact=manifest['splits'][split]
        actual=facts(root,split,fact['source_dataset_id'],fact['leaf_sources'],fact.get('preserved',False))
        if any(actual[k]!=fact[k] for k in ('image_count','annotation_count','source_type_summary','annotation_sha256','image_inventory_sha256')): raise ValueError(f'{split}のmanifestが不一致です')
        for f in (root/split/'images').iterdir():
            h=reg.sha(f)
            if h in hashes: raise ValueError(f'split間の重複画像: {f}')
            hashes.add(h)
        found[split]=actual
    if jd({'splits':{s:{k:found[s][k] for k in ('source_dataset_id','image_inventory_sha256','annotation_sha256')} for s in SPLITS}})!=manifest['dataset_id']: raise ValueError('dataset_idが不一致です')
    return {'valid':True,'counts':{s:found[s]['image_count'] for s in SPLITS},'dataset_id':manifest['dataset_id']}

def normalize_version_payload(root:Path):
    """Write canonical category IDs/names and refresh dependent manifest hashes."""
    merged=(root/'manifest.json').is_file() and not (root/'dataset_manifest.json').exists()
    paths=[root/'annotations'/'instances.json'] if merged else [root/s/'labels'/'instances.json' for s in SPLITS]
    changed=False
    for path in paths:
        value=json.loads(path.read_text(encoding='utf-8'))
        mapping=category_mapping(path,value)
        if len(mapping)!=37 or len(set(mapping.values()))!=37: raise ValueError('カテゴリの対応が一対一ではありません')
        if any(c['name']!=CATEGORIES[c['id']-1]['name'] for c in value['categories'] if c['id'] in range(1,38)) or any(k!=v+1 for k,v in mapping.items()):
            for ann in value['annotations']: ann['category_id']=mapping[ann['category_id']]+1
            value['categories']=[dict(c) for c in CATEGORIES]
            write_json(path,value)
            changed=True
    if merged:
        path=root/'manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
        digest=jd(json.loads((root/'annotations'/'instances.json').read_text(encoding='utf-8')))
        previous=manifest.get('annotation_sha256')
        manifest['annotation_sha256']=digest
        if changed or previous!=digest:
            write_json(path,manifest)
    else:
        path=root/'dataset_manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
        for split in SPLITS:
            manifest['splits'][split]['annotation_sha256']=jd(json.loads((root/split/'labels'/'instances.json').read_text(encoding='utf-8')))
        manifest['dataset_id']=jd({'splits':{s:{k:manifest['splits'][s][k] for k in ('source_dataset_id','image_inventory_sha256','annotation_sha256')} for s in SPLITS}})
        if changed or json.loads(path.read_text(encoding='utf-8'))!=manifest:
            write_json(path,manifest)
    normalize_mapping_config(root/'dataset.yaml')

def create(parent=None,batch_ids=None,image_ids=None,test_real=0,test_synthetic=0,val_ratio=.1,seed=42,keep_test=True,description='',progress=None):
    with reg.connect() as db:
        version_id=next_id(db)
        parent_info=db.execute('SELECT * FROM versions WHERE id=?',(parent,)).fetchone() if parent else None
        if parent and not parent_info: raise ValueError('親バージョンがありません')
        parent_items=parent_rows(db,parent) if parent else []
        if parent and parent_info['kind'] in ('merged','unpartitioned'): keep_test=False
        fixed=bool(parent and keep_test and parent_info['kind']=='completed')
        parent_manifest=json.loads(parent_info['manifest_json']) if parent_info else {}
    selected=rows_from_db(batch_ids,image_ids)
    if parent and parent_info['kind']=='unpartitioned':
        with reg.connect() as db:
            for r in parent_items:
                valid=db.execute("SELECT 1 FROM annotations WHERE image_id=? AND content_sha256=? AND status='reviewed' LIMIT 1",(r['image_id'],r['content_sha256'])).fetchone()
                if not valid: raise ValueError(f"未分割バージョンの画像は確認済みが必要です: {r['filename']}")
    if parent and parent_info['kind'] in ('merged','unpartitioned'):
        selected += [{**r,'content_sha256':r['annotation_sha256']} for r in parent_items]
        parent_items=[]
    chosen=split_rows(selected,parent_items,fixed,test_real,test_synthetic,val_ratio,seed)
    if progress: progress(1)
    target=core.DATA/'versions'/version_id;target.parent.mkdir(parents=True,exist_ok=True)
    staged=Path(tempfile.mkdtemp(prefix='.version-',dir=target.parent))
    try:
        for index,split in enumerate(SPLITS,2):
            if split=='test' and fixed:
                shutil.copytree(reg.path(parent_info['path'])/'test',staged/'test')
            else: render_split(staged,split,[r for r in chosen if r['split']==split])
            if progress: progress(index)
        source_id=jd(sorted((r['sha256'],r['content_sha256']) for r in chosen))
        leaves=[]
        for r in selected:
            try: leaves.extend(json.loads(r.get('provenance') or '{}').get('leaf_sources',[]))
            except ValueError: pass
        if parent_manifest.get('leaf_sources'): leaves.extend(parent_manifest['leaf_sources'])
        leaves=list({json.dumps(x,sort_keys=True):x for x in leaves}.values())
        if not leaves: leaves=[{'source_id':source_id,'source_digest':source_id}]
        split_facts={s:(dict(parent_manifest['splits']['test'],preserved=True) if s=='test' and fixed else facts(staged,s,source_id,leaves)) for s in SPLITS}
        identity={'splits':{s:{k:split_facts[s][k] for k in ('source_dataset_id','image_inventory_sha256','annotation_sha256')} for s in SPLITS}}
        manifest={'manifest_version':2,'dataset_id':jd(identity),'source_dataset_id':source_id,'leaf_sources':leaves,'seed':seed,'ratios':{'train':1-val_ratio,'val':val_ratio,'test':0},'requested_test_count':None,'requested_test_source_types':None if fixed else {'synthetic':test_synthetic,'real':test_real},'requested_val_source_types':None,'rebuild_test':not fixed,'created_at':reg.now(),'splits':split_facts,'parent_version':parent,'description':description,'fixed_test':fixed}
        write_json(staged/'dataset_manifest.json',manifest)
        validate(staged)
        if progress: progress(5)
        test_id=jd({'annotation':split_facts['test']['annotation_sha256'],'images':split_facts['test']['image_inventory_sha256']})
        if fixed and test_id!=parent_info['test_id']: raise ValueError('固定TESTが変化しました')
        staged.replace(target)
        try:
            with reg.connect() as db:
                db.execute('INSERT INTO versions(id,parent_id,kind,path,description,created_at,manifest_json,dataset_hash,test_id,status) VALUES(?,?,?,?,?,?,?,?,?,?)',(version_id,parent,'completed',reg.rel(target),description,reg.now(),json.dumps(manifest),manifest['dataset_id'],test_id,'complete'))
                for r in chosen:
                    db.execute('INSERT INTO version_images(version_id,image_id,split,image_sha256,annotation_sha256,content_json,source_type) VALUES(?,?,?,?,?,?,?)',(version_id,r['image_id'],r['split'],r['sha256'],r['content_sha256'],r['content_json'],r['source_type']))
        except Exception: shutil.rmtree(target); raise
        return {'id':version_id,'manifest':manifest,'test_id':test_id}
    finally: shutil.rmtree(staged,ignore_errors=True)

def create_from_reference(parent:str, reference_path:str, description:str='', progress=None):
    """Rebuild an exact split from registered sources, using an external dataset read-only."""
    reference=Path(reference_path).expanduser()
    if reference.is_symlink() or not reference.is_dir(): raise ValueError('参照datasetフォルダーがありません')
    reference=reference.resolve()
    if any(p.is_symlink() for p in reference.rglob('*')): raise ValueError('参照datasetにシンボリックリンクがあります')
    reference_result=validate(reference,allow_legacy=True)
    if progress: progress(1)
    with reg.connect() as db:
        parent_info=db.execute('SELECT kind FROM versions WHERE id=?',(parent,)).fetchone()
        if not parent_info or parent_info['kind']!='unpartitioned':
            raise ValueError('未分割の親バージョンを選択してください')
        parent_items=parent_rows(db,parent)
        version_id=next_id(db)
    current={row['image_id']:row for row in rows_from_db(image_ids={row['image_id'] for row in parent_items})}
    for row in parent_items:
        registered=current.get(row['image_id'])
        if not registered or registered['content_sha256']!=row['annotation_sha256']:
            raise ValueError(f"確認済みが必要です: {row['filename']}")
    by_hash={row['image_sha256']:row for row in parent_items}
    if len(by_hash)!=len(parent_items): raise ValueError('親バージョンに重複画像があります')
    assigned=[]
    reference_manifest=json.loads((reference/'dataset_manifest.json').read_text(encoding='utf-8'))
    split_documents={}
    for split in SPLITS:
        coco=json.loads((reference/split/'labels'/'instances.json').read_text(encoding='utf-8'))
        split_documents[split]=coco
        class_map=category_mapping(reference/split/'labels'/'instances.json',coco)
        images={image['id']:image for image in coco['images']}
        annotations={image_id:[] for image_id in images}
        for annotation in coco['annotations']:
            image=images[annotation['image_id']]
            x,y,w,h=annotation['bbox']
            annotations[image['id']].append({'class_id':class_map[annotation['category_id']],'x':x/image['width'],'y':y/image['height'],'w':w/image['width'],'h':h/image['height']})
            annotation['category_id']=class_map[annotation['category_id']]+1
        coco['categories']=[dict(c) for c in CATEGORIES]
        for image in coco['images']:
            name=Path(image['file_name'])
            if len(name.parts)!=1: raise ValueError(f'参照datasetの画像名が不正です: {name}')
            image_hash=reg.sha(reference/split/'images'/name)
            row=by_hash.get(image_hash)
            if not row: raise ValueError(f'元データに画像がありません: {split}/{name}')
            if row['source_type']!=image.get('source_type') or reg.box_digest(annotations[image['id']])!=row['annotation_sha256']:
                raise ValueError(f'元データとannotationが違います: {split}/{name}')
            assigned.append((split,name,row))
    if len(assigned)!=len(parent_items) or len({row['image_id'] for _,_,row in assigned})!=len(parent_items):
        raise ValueError('参照datasetと親バージョンの画像集合が一致しません')
    if progress: progress(2)
    target=core.DATA/'versions'/version_id
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists(): raise ValueError('バージョン番号が競合しました')
    staged=Path(tempfile.mkdtemp(prefix='.reference-version-',dir=target.parent))
    try:
        for split in SPLITS:
            for _,name,row in (item for item in assigned if item[0]==split):
                source=reg.path(row['path'])
                if reg.sha(source)!=row['image_sha256']: raise ValueError(f'元画像が変更されました: {source}')
                destination=staged/split/'images'/name
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,destination)
            write_json(staged/split/'labels'/'instances.json',split_documents[split])
            if progress: progress({'train':3,'val':4,'test':5}[split])
        manifest={**reference_manifest,'created_at':reg.now(),'parent_version':parent,'description':description,
                  'fixed_test':True,'reference_dataset_id':reference_result['dataset_id']}
        write_json(staged/'dataset_manifest.json',manifest)
        normalize_version_payload(staged)
        manifest=json.loads((staged/'dataset_manifest.json').read_text(encoding='utf-8'))
        result=validate(staged)
        if result['dataset_id']!=manifest['dataset_id']:
            raise ValueError('正規化後のdataset_idが一致しません')
        test=manifest['splits']['test']
        test_id=jd({'annotation':test['annotation_sha256'],'images':test['image_inventory_sha256']})
        staged.replace(target)
        try:
            with reg.connect() as db:
                db.execute('INSERT INTO versions(id,parent_id,kind,path,description,created_at,manifest_json,dataset_hash,test_id,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (version_id,parent,'completed',reg.rel(target),description,manifest['created_at'],json.dumps(manifest),manifest['dataset_id'],test_id,'complete'))
                for split,_,row in assigned:
                    db.execute('INSERT INTO version_images(version_id,image_id,split,image_sha256,annotation_sha256,content_json,source_type) VALUES(?,?,?,?,?,?,?)',
                               (version_id,row['image_id'],split,row['image_sha256'],row['annotation_sha256'],row['content_json'],row['source_type']))
        except Exception:
            shutil.rmtree(target)
            raise
        if progress: progress(6)
        return {'id':version_id,'dataset_id':manifest['dataset_id'],'test_id':test_id,'counts':result['counts']}
    finally:
        shutil.rmtree(staged,ignore_errors=True)

def import_version(value:str,mode:str,name:str,source_type=None,expected=None,progress=None):
    root=importer.source_path(value,mode);scan=importer.inspect(root,source_type)
    if scan['kind'] not in ('merged','dataset'): raise ValueError('mergedか完成datasetを指定してください')
    if scan['errors'] or scan['conflicts']: raise ValueError(str(scan['errors']+scan['conflicts']))
    token=importer.preview(value,mode,source_type)['preview_id']
    if token!=expected: raise ValueError('プレビュー後に入力が変更されました')
    if progress: progress(1)
    with reg.connect() as db: version_id=next_id(db)
    target=core.DATA/'versions'/version_id;target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists(): raise ValueError('バージョン番号が競合しました')
    staged=Path(tempfile.mkdtemp(prefix='.import-version-',dir=target.parent))
    installed=False
    try:
        payload=staged/'payload'
        if scan['kind']=='dataset':
            payload.mkdir()
            for split in SPLITS: shutil.copytree(root/split,payload/split)
            shutil.copy2(root/'dataset_manifest.json',payload/'dataset_manifest.json')
            if (root/'dataset.yaml').is_file(): shutil.copy2(root/'dataset.yaml',payload/'dataset.yaml')
        else: shutil.copytree(root,payload)
        normalize_version_payload(payload)
        copied=importer.inspect(payload,source_type)
        if [(r['rel'],r['sha256']) for r in copied['rows']]!=[(r['rel'],r['sha256']) for r in scan['rows']]: raise ValueError('コピー検証失敗')
        payload.replace(target);installed=True
        if progress: progress(2)
        manifest_path=target/('manifest.json' if scan['kind']=='merged' else 'dataset_manifest.json')
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'));kind='merged' if scan['kind']=='merged' else 'completed'
        if kind=='completed': validate(target)
        test_id=None
        if kind=='completed':
            f=json.loads((target/'dataset_manifest.json').read_text())['splits']['test'];test_id=jd({'annotation':f['annotation_sha256'],'images':f['image_inventory_sha256']})
        try:
            with reg.connect() as db:
                db.execute('INSERT INTO versions(id,kind,path,description,created_at,manifest_json,dataset_hash,test_id,status) VALUES(?,?,?,?,?,?,?,?,?)',(version_id,kind,reg.rel(target),name,reg.now(),json.dumps(manifest),manifest.get('dataset_id'),test_id,'complete' if kind=='completed' else 'unpartitioned'))
                for r in scan['rows']:
                    f=target/r['rel']; old=db.execute('SELECT id FROM images WHERE sha256=?',(r['sha256'],)).fetchone()
                    if old: image_id=old[0]
                    else: image_id=db.execute('INSERT INTO images(sha256,path,filename,width,height,source_type,created_at) VALUES(?,?,?,?,?,?,?)',(r['sha256'],reg.rel(f),f.name,r['width'],r['height'],r['source_type'],reg.now())).lastrowid
                    db.execute('INSERT INTO version_images(version_id,image_id,split,image_sha256,annotation_sha256,content_json,source_type) VALUES(?,?,?,?,?,?,?)',(version_id,image_id,r['split'] if kind=='completed' else 'pool',r['sha256'],reg.box_digest(r['boxes']),json.dumps(r['boxes']),r['source_type']))
        except Exception: shutil.rmtree(target); raise
    except Exception:
        if installed: shutil.rmtree(target,ignore_errors=True)
        raise
    finally: shutil.rmtree(staged,ignore_errors=True)
    if mode=='move': shutil.rmtree(root)
    if progress: progress(3)
    return {'id':version_id,'kind':kind}
