import json, sys, zipfile
from pathlib import Path
import pytest
from PIL import Image
from app import core, importer, registry, versions, export, main
from app.schema import CATEGORIES, TILE_CLASSES, coco_boxes, save_coco

@pytest.fixture
def root(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DATA',tmp_path/'data')
    monkeypatch.setattr(core,'EDITOR',tmp_path/'editor')
    return tmp_path

def yolo(root,name,count):
    p=root/name;(p/'images').mkdir(parents=True);(p/'labels').mkdir()
    (p/'data.yaml').write_text('names: '+json.dumps(list(TILE_CLASSES))+'\nnc: 37\n')
    for i in range(count):
        Image.new('RGB',(40,30),(i*31+len(name),4,7)).save(p/'images'/f'{i}.png')
        (p/'labels'/f'{i}.txt').write_text('0 0.5 0.5 0.4 0.4\n')
    return p

def coco(root,name,count,typ):
    p=root/name;(p/'images').mkdir(parents=True);images=[];anns=[]
    for i in range(count):
        Image.new('RGB',(40,30),(i*32+len(name),8,9)).save(p/'images'/f'{i}.png')
        images.append({'id':i+1,'file_name':f'{i}.png','width':40,'height':30,'source_type':typ})
        anns.append({'id':i+1,'image_id':i+1,'category_id':1,'bbox':[5,5,15,15],'area':225,'iscrowd':0})
    (p/'annotations').mkdir();(p/'annotations'/'instances.json').write_text(json.dumps({'images':images,'annotations':anns,'categories':CATEGORIES}))
    return p

def register(p,name,typ):
    v=importer.preview(str(p),'copy',typ);assert not v['errors'];return importer.register(str(p),'copy',typ,name,v['preview_id'])

def review(name):
    with registry.connect() as db:
        paths=[r[0] for r in db.execute('SELECT bi.path FROM batch_images bi JOIN batches b ON b.id=bi.batch_id WHERE b.name=?',(name,))]
    for path in paths: core.set_status(core.DATA/path,'reviewed')

def test_yolo_import_conflict_and_rollback(root):
    p=yolo(root,'input',3);register(p,'first','real');register(p,'second','real')
    with registry.connect() as db: assert db.execute('SELECT COUNT(*) FROM images').fetchone()[0]==3
    (p/'labels'/'0.txt').write_text('1 0.5 0.5 0.4 0.4\n')
    preview=importer.preview(str(p),'copy','real');assert preview['conflicts']
    assert preview['import_count']==2
    result=importer.register(str(p),'copy','real','third',preview['preview_id'])
    assert result['image_count']==2 and result['excluded_conflicts']==['images/0.png']
    stored=core.DATA/'sources'/'third'
    assert not (stored/'images'/'0.png').exists()
    assert not (stored/'labels'/'0.txt').exists()
    assert (p/'images'/'0.png').exists() and (p/'labels'/'0.txt').exists()
    assert importer.inspect(stored,'real')['image_count']==2
    with registry.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM batch_images WHERE batch_id=?',(result['batch_id'],)).fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM images').fetchone()[0]==3

def test_import_normalizes_legacy_yolo_without_moving_annotation_ids(root):
    p=yolo(root,'legacy_yolo',1)
    value=json.loads((p/'data.yaml').read_text().split('names: ',1)[1].splitlines()[0])
    value[34:]=['5mr','5pr','5sr']
    (p/'data.yaml').write_text('names: '+json.dumps(value)+'\nnc: 37\n')
    (p/'labels'/'0.txt').write_text('34 0.5 0.5 0.4 0.4\n')
    register(p,'legacy_yolo','real')
    stored=core.DATA/'sources'/'legacy_yolo'
    assert core.classes(stored)[34:]==['red5m','red5p','red5s']
    assert (stored/'labels'/'0.txt').read_text().startswith('34 ')
    assert importer.inspect(stored,'real')['rows'][0]['boxes'][0]['class_id']==34
    assert '5mr' in (p/'data.yaml').read_text()

def test_import_normalizes_legacy_coco_without_moving_annotation_ids(root):
    p=coco(root,'legacy_coco',1,'real')
    path=p/'annotations'/'instances.json';value=json.loads(path.read_text())
    for i,name in enumerate(('5mr','5pr','5sr'),34): value['categories'][i]['name']=name
    value['annotations'][0]['category_id']=35
    path.write_text(json.dumps(value))
    register(p,'legacy_coco','real')
    stored=core.DATA/'sources'/'legacy_coco'/'annotations'/'instances.json'
    saved=json.loads(stored.read_text())
    assert [item['name'] for item in saved['categories']][-3:]==['red5m','red5p','red5s']
    assert saved['annotations'][0]['category_id']==35
    assert importer.inspect(stored.parent.parent,'real')['rows'][0]['boxes'][0]['class_id']==34

def test_conflicting_yolo_move_retains_original(root):
    p=yolo(root,'move_input',2);register(p,'first','real')
    (p/'labels'/'0.txt').write_text('1 0.5 0.5 0.4 0.4\n')
    preview=importer.preview(str(p),'move','real')
    result=importer.register(str(p),'move','real','second',preview['preview_id'])
    assert result['source_retained'] is True
    assert p.exists() and (p/'images'/'0.png').exists()
    assert not (core.DATA/'sources'/'second'/'images'/'0.png').exists()

def test_all_conflicting_images_are_not_registered(root):
    p=yolo(root,'all_conflicts',1);register(p,'first','real')
    (p/'labels'/'0.txt').write_text('1 0.5 0.5 0.4 0.4\n')
    preview=importer.preview(str(p),'copy','real')
    assert preview['import_count']==0
    with pytest.raises(importer.ImportErrorDetail,match='登録できる画像がありません'):
        importer.register(str(p),'copy','real','second',preview['preview_id'])
    assert not (core.DATA/'sources'/'second').exists()

def test_conflicting_coco_copy_removes_image_and_annotation(root):
    p=coco(root,'coco_input',2,'real');register(p,'first','real')
    label=p/'annotations'/'instances.json';value=json.loads(label.read_text())
    value['annotations'][0]['category_id']=2
    label.write_text(json.dumps(value))
    preview=importer.preview(str(p),'copy','real')
    assert preview['conflicts']==['images/0.png']
    result=importer.register(str(p),'copy','real','second',preview['preview_id'])
    stored=core.DATA/'sources'/'second'
    assert result['image_count']==1
    assert not (stored/'images'/'0.png').exists()
    saved=json.loads((stored/'annotations'/'instances.json').read_text())
    assert [image['file_name'] for image in saved['images']]==['1.png']
    assert [annotation['image_id'] for annotation in saved['annotations']]==[2]
    assert importer.inspect(stored,'real')['image_count']==1

def test_coco_edit_preserves_other_and_backup(root):
    p=coco(root,'coco',2,'synthetic');register(p,'coco','synthetic')
    stored=core.DATA/'sources'/'coco';f=stored/'images'/'0.png';file=stored/'annotations'/'instances.json';old=json.loads(file.read_text())
    assert len(coco_boxes(file,f))==1
    boxes=[{'class_id':2,'x':.2,'y':.2,'w':.3,'h':.3},{'class_id':1,'x':.5,'y':.4,'w':.2,'h':.2}]
    save_coco(file,f,boxes,core.version(file));new=json.loads(file.read_text())
    assert [a for a in new['annotations'] if a['image_id']==2]==[a for a in old['annotations'] if a['image_id']==2]
    assert len(list((core.DATA/'backups').rglob('*.bak')))==1
    with pytest.raises(Exception): save_coco(file,f,[], 'old-version')

def test_coco_image_list_reads_annotation_once(root,monkeypatch):
    from app import schema
    folder=coco(core.DATA/'sources','listed',20,'synthetic')
    dataset=next(d for d in core.datasets() if d['path']=='sources/listed')
    original=schema.read_coco
    calls=[]
    def counted(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(schema,'read_coco',counted)
    rows=core.images(dataset['id'])
    assert len(rows)==20
    assert all(row['bbox_count']==1 and not row['label_error'] for row in rows)
    assert calls==[folder/'annotations'/'instances.json']

def test_version_fixed_test_archive_and_training_validation(root):
    register(yolo(root,'real',5),'real','real');register(coco(root,'synthetic',5,'synthetic'),'synthetic','synthetic')
    review('real');review('synthetic')
    first=versions.create(test_real=1,test_synthetic=1,val_ratio=.3,seed=7)
    assert versions.validate(core.DATA/'versions'/first['id'])['valid']
    t1=(core.DATA/'versions'/first['id']/'test'/'labels'/'instances.json').read_bytes()
    register(yolo(root,'extra',3),'extra','real');review('extra')
    second=versions.create(parent=first['id'],batch_ids=[3],keep_test=True,val_ratio=.3,seed=7)
    assert second['test_id']==first['test_id']
    assert (core.DATA/'versions'/second['id']/'test'/'labels'/'instances.json').read_bytes()==t1
    rebuilt=versions.create(parent=second['id'],keep_test=False,test_real=2,test_synthetic=1,val_ratio=.2,seed=8)
    assert rebuilt['test_id']!=first['test_id']
    with pytest.raises(ValueError): versions.create(test_real=500)
    meta=export.archive(second['id']);archive=core.DATA/'exports'/meta['file'];assert registry.sha(archive)==meta['sha256']
    with zipfile.ZipFile(archive) as z:
        assert 'dataset_manifest.json' in z.namelist();extracted=root/'extract';z.extractall(extracted)
    training=Path(__file__).resolve().parents[4]/'mahjong-tile-ai'
    if training.exists():
        sys.path.insert(0,str(training/'src'))
        from mahjong_tile_ai.dataset_validation import validate_coco_split
        for split in ('train','val','test'): assert validate_coco_split(extracted/split,'labels/instances.json').valid
        training_python=training/'.venv'/'bin'/'python'
        if training_python.exists():
            import subprocess
            code='from mahjong_tile_ai.dataset_tools import validate_dataset; import sys; assert validate_dataset(sys.argv[1]).valid'
            subprocess.run([str(training_python),'-c',code,str(extracted)],check=True)
    assert versions.validate(extracted)['valid']

def test_prepared_and_merged_import(root):
    prepared=coco(root,'prepared',2,'real');(prepared/'manifest.json').write_text(json.dumps({'dataset_type':'prepared','image_count':2,'annotation_count':2,'source_type_summary':{'real':2,'synthetic':0},'leaf_sources':[{'source_id':'x'}]}))
    register(prepared,'prepared','real')
    merged=coco(root,'merged',2,'real');(merged/'manifest.json').write_text(json.dumps({'dataset_type':'merged','image_count':2,'annotation_count':2,'source_type_summary':{'real':2,'synthetic':0},'leaf_sources':[{'source_id':'y'}]}))
    preview=importer.preview(str(merged),'copy','real');item=versions.import_version(str(merged),'copy','merged','real',preview['preview_id'])
    assert item['kind']=='merged'

def test_drive_upload_order_and_failure(root,monkeypatch):
    from types import SimpleNamespace
    exports=core.DATA/'exports';exports.mkdir(parents=True)
    archive=exports/'mahjong_dataset_sample.zip';archive.write_bytes(b'archive')
    old={'file':'old.zip'};(exports/'latest.json').write_text(json.dumps(old))
    with registry.connect() as db:
        db.execute("INSERT INTO versions(id,kind,path,created_at,manifest_json,status) VALUES('v001','completed','versions/v001',?,'{}','complete')",(registry.now(),))
    meta={'file':archive.name,'sha256':registry.sha(archive),'snapshot_id':'sample'}
    monkeypatch.setattr(export,'archive',lambda version_id,publish_latest=True:meta)
    monkeypatch.setattr(export,'setup',lambda:{'ready':True})
    monkeypatch.setattr(export.shutil,'which',lambda x:'/usr/bin/rclone')
    calls=[]
    def failed(args,**kwargs):
        calls.append(args);return SimpleNamespace(returncode=1,stdout='',stderr='failed')
    monkeypatch.setattr(export.subprocess,'run',failed)
    with pytest.raises(ValueError): export.upload('v001',True)
    assert json.loads((exports/'latest.json').read_text())==old
    assert not any('latest.json' in ' '.join(c) for c in calls)
    def passed(args,**kwargs):
        calls.append(args)
        if 'lsjson' in args:return SimpleNamespace(returncode=0,stdout=json.dumps({'Size':archive.stat().st_size,'Hashes':{'SHA-256':meta['sha256']}}),stderr='')
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    calls.clear();monkeypatch.setattr(export.subprocess,'run',passed)
    assert export.upload('v001',True)['uploaded']
    assert 'latest.json' in calls[-1][-1]
    assert json.loads((exports/'latest.json').read_text())==meta

def test_merged_to_complete_and_completed_import(root):
    import shutil
    merged=coco(root,'merged_pool',8,'real')
    (merged/'manifest.json').write_text(json.dumps({'dataset_type':'merged','image_count':8,'annotation_count':8,'source_type_summary':{'real':8,'synthetic':0},'leaf_sources':[{'source_id':'origin','source_digest':'digest'}]}))
    v=importer.preview(str(merged),'copy','real');item=versions.import_version(str(merged),'copy','merged','real',v['preview_id'])
    completed=versions.create(parent=item['id'],test_real=1,val_ratio=.4,seed=9)
    assert versions.validate(core.DATA/'versions'/completed['id'])['valid']
    external=root/'outside_dataset';shutil.copytree(core.DATA/'versions'/completed['id'],external)
    (external/'archives').mkdir();(external/'archives'/'old.zip').write_bytes(b'old archive')
    v=importer.preview(str(external),'copy',None);assert not v['errors']
    restored=versions.import_version(str(external),'copy','imported',None,v['preview_id'])
    assert restored['kind']=='completed'
    assert not (core.DATA/'versions'/restored['id']/'archives').exists()
    with registry.connect() as db:
        old=db.execute('SELECT test_id FROM versions WHERE id=?',(completed['id'],)).fetchone()[0]
        new=db.execute('SELECT test_id FROM versions WHERE id=?',(restored['id'],)).fetchone()[0]
    assert old==new

def test_review_invalidated_after_coco_edit_and_external_drift(root):
    from app import main
    p=coco(root,'edit',4,'real');register(p,'edit','real');review('edit')
    stored=core.DATA/'sources'/'edit';file=stored/'annotations'/'instances.json';f=stored/'images'/'0.png'
    image_id=core.ident(f)
    detail=main.annotations(image_id)
    changed=[{'class_id':1,'x':.175,'y':.2,'w':.3,'h':.3}]
    main.save_annotations(image_id,main.SaveBody(boxes=changed,version=detail['version']))
    assert core.status_for(f)['status']=='unreviewed'
    core.set_status(f,'reviewed')
    assert versions.create(batch_ids=[1],test_real=1,val_ratio=.4,seed=3)['id']=='v001'
    raw=json.loads(file.read_text());raw['annotations'][0]['bbox']=[2,2,10,10];file.write_text(json.dumps(raw))
    with pytest.raises(ValueError,match='DBとファイル'): versions.create(batch_ids=[1],test_real=1,val_ratio=.4,seed=3)

def test_sources_register_idempotent_and_symlink_rejected(root):
    source=yolo(core.DATA/'sources','manual',2)
    preview=importer.preview(str(source),'register','real')
    first=importer.register(str(source),'register','real','manual',preview['preview_id'])
    second=importer.register(str(source),'register','real','manual',preview['preview_id'])
    assert first['batch_id']==second['batch_id'] and second['already_registered']
    link=root/'link';link.symlink_to(source,target_is_directory=True)
    with pytest.raises(ValueError): importer.preview(str(link),'copy','real')

def test_unpartitioned_version_waits_for_review_and_test(root):
    from fastapi import HTTPException
    from app import main
    register(yolo(root,'raw',5),'raw','real')
    draft=versions.create_unpartitioned(batch_ids=[1],description='TESTを後で設定')
    location=core.DATA/'versions'/draft['id']
    assert versions.validate(location)['valid']
    assert not (location/'test').exists()
    dataset=next(d for d in core.datasets() if d['path']=='versions/'+draft['id'])
    assert not dataset['editable']
    row=core.images(dataset['id'])[0]
    detail=main.image(row['id'])
    assert not detail['editable'] and len(detail['annotations']['boxes'])==1
    with pytest.raises(HTTPException) as error: main.status(row['id'],main.StatusBody(status='reviewed'))
    assert error.value.status_code==403
    with pytest.raises(ValueError,match='確認済み'): versions.create(parent=draft['id'],test_real=1,val_ratio=.4)
    review('raw')
    finished=versions.create(parent=draft['id'],test_real=1,val_ratio=.4,seed=2)
    assert finished['manifest']['parent_version']==draft['id']
    assert versions.validate(core.DATA/'versions'/finished['id'])['valid']

def test_rebuilds_reference_splits_and_test_from_registered_sources(root):
    register(yolo(root,'real_reference',5),'real_reference','real')
    register(coco(root,'synthetic_reference',5,'synthetic'),'synthetic_reference','synthetic')
    review('real_reference');review('synthetic_reference')
    reference=versions.create(test_real=1,test_synthetic=1,val_ratio=.3,seed=7)
    reference_path=core.DATA/'versions'/reference['id']
    original_manifest=(reference_path/'dataset_manifest.json').read_bytes()
    draft=versions.create_unpartitioned(batch_ids=[1,2])
    image=core.DATA/'sources'/'synthetic_reference'/'images'/'0.png'
    core.set_status(image,'unreviewed')
    with pytest.raises(ValueError,match='確認済み'):
        versions.create_from_reference(draft['id'],str(reference_path))
    core.set_status(image,'reviewed')
    rebuilt=versions.create_from_reference(draft['id'],str(reference_path),'同じ分割')
    output=core.DATA/'versions'/rebuilt['id']
    assert rebuilt['dataset_id']==reference['manifest']['dataset_id']
    assert rebuilt['test_id']==reference['test_id']
    assert rebuilt['counts']=={'train':reference['manifest']['splits']['train']['image_count'],
                                'val':reference['manifest']['splits']['val']['image_count'],
                                'test':reference['manifest']['splits']['test']['image_count']}
    for split in ('train','val','test'):
        assert json.loads((output/split/'labels'/'instances.json').read_text())==json.loads((reference_path/split/'labels'/'instances.json').read_text())
        assert sorted((p.name,registry.sha(p)) for p in (output/split/'images').iterdir())==sorted((p.name,registry.sha(p)) for p in (reference_path/split/'images').iterdir())
    assert (reference_path/'dataset_manifest.json').read_bytes()==original_manifest
    assert versions.validate(output)['valid']

def test_batch_confirmation_is_explicit_and_audited(root):
    register(yolo(root,'confirmed',3),'confirmed','real')
    with registry.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM annotations WHERE status='reviewed'").fetchone()[0]==0
    assert registry.confirm_batch(1)['reviewed']==3
    with registry.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM annotations WHERE status='reviewed'").fetchone()[0]==3
        assert db.execute('SELECT COUNT(*) FROM status_history').fetchone()[0]==3

def test_batch_confirmation_rejects_concurrent_annotation_edit(root,monkeypatch):
    register(yolo(root,'editing',3),'editing','real')
    original=versions.rows_from_db
    def changed(*args,**kwargs):
        rows=original(*args,**kwargs)
        file=core.DATA/'sources'/'editing'/'images'/'2.png'
        label=core.DATA/'sources'/'editing'/'labels'/'2.txt'
        label.write_text('1 0.5 0.5 0.4 0.4\n')
        registry.annotation_saved(file,[{'class_id':1,'x':.3,'y':.3,'w':.4,'h':.4}])
        return rows
    monkeypatch.setattr(versions,'rows_from_db',changed)
    with pytest.raises(ValueError,match='確認中にannotationが変更'):
        registry.confirm_batch(1)
    with registry.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM annotations WHERE status='reviewed'").fetchone()[0]==0

def test_batch_sync_updates_changed_annotations_and_resets_review(root):
    register(yolo(root,'sync_input',2),'sync_batch','real')
    review('sync_batch')
    label=core.DATA/'sources'/'sync_batch'/'labels'/'0.txt'
    label.write_text('0 0.5 0.5 0.4 0.4\n1 0.2 0.2 0.2 0.2\n')
    result=registry.sync_batch_annotations(1)
    assert '1 枚を更新' in result and '再確認が必要 1 枚' in result
    batch=registry.list_batches()[0]
    assert batch['bbox_count']==3
    assert batch['reviewed_count']==1 and batch['unreviewed_count']==1
    with registry.connect() as db:
        rows=db.execute('SELECT revision,status,content_json FROM annotations ORDER BY image_id').fetchall()
        assert [(r['revision'],r['status'],len(json.loads(r['content_json']))) for r in rows]==[(2,'unreviewed',2),(1,'reviewed',1)]
    assert '0 枚を更新' in registry.sync_batch_annotations(1)

def test_batch_sync_invalid_label_does_not_partially_update(root):
    register(yolo(root,'sync_invalid',2),'sync_invalid','real')
    stored=core.DATA/'sources'/'sync_invalid'/'labels'
    (stored/'0.txt').write_text('0 0.5 0.5 0.4 0.4\n1 0.2 0.2 0.2 0.2\n')
    (stored/'1.txt').write_text('invalid\n')
    with pytest.raises(ValueError,match='検証に失敗'):
        registry.sync_batch_annotations(1)
    assert registry.list_batches()[0]['bbox_count']==2
    with registry.connect() as db:
        assert db.execute('SELECT MAX(revision) FROM annotations').fetchone()[0]==1

def test_batch_sync_keeps_review_for_serialization_rounding(root):
    register(yolo(root,'sync_rounding',1),'sync_rounding','real')
    review('sync_rounding')
    with registry.connect() as db:
        row=db.execute('SELECT id,content_json FROM annotations').fetchone()
        boxes=json.loads(row['content_json'])
        boxes[0]['x']+=6e-10
        db.execute('UPDATE annotations SET content_json=?,content_sha256=? WHERE id=?',
                   (json.dumps(boxes),registry.box_digest(boxes),row['id']))
    assert '1 枚を更新' in registry.sync_batch_annotations(1)
    batch=registry.list_batches()[0]
    assert batch['reviewed_count']==1 and batch['bbox_count']==1
    with registry.connect() as db:
        row=db.execute('SELECT status,content_sha256 FROM annotations').fetchone()
        assert row['status']=='reviewed'
        label=core.DATA/'sources'/'sync_rounding'/'labels'/'0.txt'
        assert row['content_sha256']==registry.box_digest(core.parse_label(label.read_text(),37))

def test_editor_save_registers_serialized_label_values(root):
    register(yolo(root,'precise_input',1),'precise_input','real')
    image=core.DATA/'sources'/'precise_input'/'images'/'0.png'
    label=image.parent.parent/'labels'/'0.txt'
    boxes=[{'class_id':1,'x':.12345678912345,'y':.2345678912345,'w':.12345678912345,'h':.2345678912345}]
    main.save_annotations(core.ident(image),main.SaveBody(boxes=boxes,version=core.version(label)))
    core.set_status(image,'reviewed')
    with registry.connect() as db:
        stored=db.execute('SELECT content_sha256 FROM annotations').fetchone()[0]
    assert stored==registry.box_digest(core.parse_label(label.read_text(),37))
    assert len(versions.rows_from_db([1]))==1
