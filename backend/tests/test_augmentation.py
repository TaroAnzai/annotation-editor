import json
import hashlib
from pathlib import Path
import pytest
from PIL import Image
from app import augmentation, core, importer, registry, versions, main
from app.schema import CATEGORIES, TILE_CLASSES

@pytest.fixture
def root(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DATA',tmp_path/'data')
    monkeypatch.setattr(core,'EDITOR',tmp_path/'editor')
    return tmp_path

def source(root,name='source',count=8):
    p=core.DATA/'sources'/name
    (p/'images').mkdir(parents=True)
    (p/'annotations').mkdir()
    coco={'images':[],'annotations':[],'categories':CATEGORIES}
    for i in range(count):
        Image.new('RGB',(80,60),((20+i*12)%256,80+i//20,(110+sum(name.encode()))%256)).save(p/'images'/f'{i}.png')
        coco['images'].append({'id':i+1,'file_name':f'{i}.png','width':80,'height':60,'source_type':'real'})
        coco['annotations'].append({'id':i+1,'image_id':i+1,'category_id':i%37+1,'bbox':[20,15,20,20],'area':400,'iscrowd':0})
    (p/'annotations'/'instances.json').write_text(json.dumps(coco))
    preview=importer.preview(str(p),'register',None)
    importer.register(str(p),'register',None,name,preview['preview_id'])
    return p,core.ident(p)

def config(active):
    value={key:{**entry,'enabled':key==active} for key,entry in augmentation.DEFAULTS.items()}
    return value

def digest_tree(path):
    return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}

@pytest.mark.parametrize('active',('brightness','contrast'))
def test_photometric_bbox_unchanged(root,active):
    p,dataset=source(root,count=1)
    result=augmentation.create(dataset,active,transforms=config(active))
    coco=json.loads((core.DATA/'sources'/active/'annotations'/'instances.json').read_text())
    assert coco['annotations'][0]['bbox']==[20,15,20,20]
    assert coco['annotations'][0]['category_id']==1
    assert result['image_count']==1

def test_rotation_bbox_geometry_and_classes(root):
    box={'class_id':36,'x':.25,'y':.25,'w':.25,'h':1/3}
    transformed,visible,inflation=augmentation.rotate_box(box,80,60,90)
    assert transformed['class_id']==36
    assert transformed['x']==pytest.approx(.3125)
    assert transformed['y']==pytest.approx(.5)
    assert transformed['w']==pytest.approx(.25)
    assert transformed['h']==pytest.approx(1/3)
    assert visible==pytest.approx(1)
    assert inflation==pytest.approx(1)
    image=Image.new('RGB',(80,60))
    changed,boxes,_=augmentation.transform(image,[box],{'brightness':None,'contrast':None,'rotation':3})
    assert changed.size==(80,60)
    assert all(0<=b[k]<=1 for b in boxes for k in ('x','y','w','h'))
    assert all(b['x']+b['w']<=1 and b['y']+b['h']<=1 for b in boxes)

def test_reproducible_source_untouched_and_no_overwrite(root):
    p,dataset=source(root,count=2); before=digest_tree(p)
    augmentation.create(dataset,'first',seed=17)
    output=core.DATA/'sources'/'first'; first=digest_tree(output)
    assert digest_tree(p)==before
    with pytest.raises(ValueError,match='同名'): augmentation.create(dataset,'first',seed=17)
    assert digest_tree(output)==first
    # A different run can recreate the same bytes after removing the first output.
    registry.delete_batch(next(b['id'] for b in registry.list_batches() if b['name']=='first'))
    augmentation.create(dataset,'second',seed=17)
    second=digest_tree(core.DATA/'sources'/'second')
    assert first['annotations/instances.json']==second['annotations/instances.json']
    assert first['images/'+next(k for k in first if k.startswith('images/')).split('/')[-1]]==second['images/'+next(k for k in second if k.startswith('images/')).split('/')[-1]]

def test_failed_generation_not_registered(root,monkeypatch):
    p,dataset=source(root,count=1)
    monkeypatch.setattr(augmentation,'transform',lambda *args:(None,None,['rejected']))
    with pytest.raises(ValueError,match='品質基準'): augmentation.create(dataset,'broken')
    assert not (core.DATA/'sources'/'broken').exists()
    assert all(b['name']!='broken' for b in registry.list_batches())

def test_preview_is_read_only_and_contains_bbox_images(root):
    p,dataset=source(root,count=1)
    before=digest_tree(p)
    preview=augmentation.render_preview(dataset,42,augmentation.DEFAULTS)
    assert preview['original'].startswith(b'\x89PNG')
    assert preview['augmented'].startswith(b'\x89PNG')
    assert preview['boxes'][0]['class_id']==0
    assert digest_tree(p)==before

def test_editor_and_version_keep_lineage_together(root):
    p,dataset=source(root,count=12)
    augmentation.create(dataset,'aug',seed=4)
    generated=core.DATA/'sources'/'aug'
    entry=next(d for d in core.datasets() if d['path']=='sources/aug')
    assert entry['editable'] and entry['format']=='coco'
    picture=core.images(entry['id'])[0]
    d,folder,file=core.image(picture['id'])
    detail=core.label_info(d,folder,file)
    assert detail['boxes'] and not detail['error']
    assert core.save(d,folder,file,detail['boxes'],detail['version'])['boxes']==detail['boxes']
    # Restore DB/file coherence after the edit and mark all images reviewed.
    for batch in ('source','aug'):
        with registry.connect() as db:
            paths=[r[0] for r in db.execute('SELECT bi.path FROM batch_images bi JOIN batches b ON b.id=bi.batch_id WHERE b.name=?',(batch,))]
        for path in paths: core.set_status(core.DATA/path,'reviewed')
    with registry.connect() as db: ids=[r[0] for r in db.execute("SELECT id FROM batches WHERE name IN ('source','aug')")]
    result=versions.create(batch_ids=ids,test_real=2,val_ratio=.5,seed=4)
    assert versions.validate(core.DATA/'versions'/result['id'])['valid']
    with registry.connect() as db:
        rows=[dict(r) for r in db.execute('SELECT vi.image_id,vi.split,i.sha256 FROM version_images vi JOIN images i ON i.id=vi.image_id WHERE vi.version_id=?',(result['id'],))]
    groups=versions.origin_groups(rows)
    by_group={}
    for row in rows: by_group.setdefault(groups[row['image_id']],set()).add(row['split'])
    assert all(len(splits)==1 for splits in by_group.values())
    assert len(rows)==24
    manifest=json.loads((generated/'augmentation.json').read_text())
    assert manifest['images'][0]['source_sha256'] and manifest['images'][0]['parameters']

def test_all_37_class_ids_and_duplicate_signature(root):
    p,dataset=source(root,count=37)
    augmentation.create(dataset,'all_classes',seed=8,transforms=config('brightness'))
    coco=json.loads((core.DATA/'sources'/'all_classes'/'annotations'/'instances.json').read_text())
    assert sorted(a['category_id'] for a in coco['annotations'])==list(range(1,38))
    assert coco['categories']==CATEGORIES
    with pytest.raises(ValueError,match='既に登録'): augmentation.create(dataset,'duplicate_run',seed=8,transforms=config('brightness'))
    assert not (core.DATA/'sources'/'duplicate_run').exists()

def test_multiple_sources_produce_one_dataset_with_lineage(root):
    first,first_id=source(root,'first_source',count=2)
    second,second_id=source(root,'second_source',count=3)
    result=augmentation.create([second_id,first_id],'combined',variants=2,seed=6)
    output=core.DATA/'sources'/'combined'
    coco=json.loads((output/'annotations'/'instances.json').read_text())
    manifest=json.loads((output/'augmentation.json').read_text())
    assert result['image_count']==10
    assert len(coco['images'])==10
    assert len({image['file_name'] for image in coco['images']})==10
    assert manifest['source_datasets']==['sources/first_source','sources/second_source']
    assert {item['source_dataset'] for item in manifest['images']}==set(manifest['source_datasets'])
    assert len([batch for batch in registry.list_batches() if batch['name']=='combined'])==1
    preview=augmentation.render_preview([second_id,first_id],6,None,index=2)
    assert preview['image'].startswith('sources/second_source/')
    assert preview['total']==5
    with pytest.raises(ValueError,match='既に登録'):
        augmentation.create([first_id,second_id],'combined_again',variants=2,seed=6)
    with pytest.raises(ValueError,match='重複'):
        augmentation.create([first_id,first_id],'invalid')

def test_fixed_test_and_val_follow_origin(root):
    p,dataset=source(root,count=12)
    with registry.connect() as db:
        source_id=db.execute("SELECT id FROM batches WHERE name='source'").fetchone()[0]
        paths=[r[0] for r in db.execute('SELECT path FROM batch_images WHERE batch_id=?',(source_id,))]
    for path in paths: core.set_status(core.DATA/path,'reviewed')
    parent=versions.create(batch_ids=[source_id],test_real=2,val_ratio=.5,seed=4)
    augmentation.create(dataset,'aug_fixed',seed=9)
    with registry.connect() as db:
        augmented_id=db.execute("SELECT id FROM batches WHERE name='aug_fixed'").fetchone()[0]
        paths=[r[0] for r in db.execute('SELECT path FROM batch_images WHERE batch_id=?',(augmented_id,))]
    for path in paths: core.set_status(core.DATA/path,'reviewed')
    child=versions.create(parent=parent['id'],batch_ids=[augmented_id],keep_test=True,val_ratio=.5,seed=4)
    with registry.connect() as db:
        rows=[dict(r) for r in db.execute('SELECT vi.image_id,vi.split,i.sha256 FROM version_images vi JOIN images i ON i.id=vi.image_id WHERE vi.version_id=?',(child['id'],))]
    groups=versions.origin_groups(rows)
    by_group={}
    for row in rows: by_group.setdefault(groups[row['image_id']],set()).add(row['split'])
    assert all(len(splits)==1 for splits in by_group.values())
    assert versions.validate(core.DATA/'versions'/child['id'])['valid']
