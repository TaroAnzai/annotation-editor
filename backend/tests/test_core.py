from pathlib import Path
import pytest
from PIL import Image
from fastapi import HTTPException
from app import core
from app import main

@pytest.fixture
def sample(tmp_path,monkeypatch):
    data=tmp_path/'data';editor=tmp_path/'annotation-editor';p=data/'sample'/'roboflow_import'
    (p/'images').mkdir(parents=True);(p/'labels').mkdir()
    (p/'data.yaml').write_text('nc: 2\nnames: [1m, 2m]\n')
    Image.new('RGB',(200,100)).save(p/'images'/'10.jpg')
    Image.new('RGB',(200,100)).save(p/'images'/'2.jpg')
    (p/'labels'/'2.txt').write_text('0 0.50000000 0.50000000 0.20000000 0.40000000\n')
    monkeypatch.setattr(core,'DATA',data);monkeypatch.setattr(core,'EDITOR',editor)
    return p,editor

def test_detection_and_pairing(sample):
    p,_=sample;tree=core.folders();assert tree['children'][0]['name']=='sample'
    ds=core.datasets();assert len(ds)==1 and ds[0]['editable']
    rows=core.images(ds[0]['id']);assert [x['name'] for x in rows]==['2.jpg','10.jpg']
    assert rows[0]['label_exists'] and not rows[1]['label_exists']
    assert rows[1]['bbox_count']==0

def test_yaml_and_parse(sample):
    p,_=sample;assert core.classes(p)==['1m','2m']
    b=core.parse_label('1 0.5 0.5 0.2 0.4\n',2)[0]
    assert b=={'class_id':1,'x':0.4,'y':0.3,'w':0.2,'h':0.4}
    assert b['x']+b['w']/2==pytest.approx(.5)
    with pytest.raises(HTTPException):core.parse_label('3 .5 .5 .2 .4',2)
    with pytest.raises(HTTPException):core.parse_label('0 .01 .5 .2 .4',2)
    with pytest.raises(HTTPException):core.validate_boxes([{'class_id':3,'x':0,'y':0,'w':.1,'h':.1}],2)

def test_parse_label_clips_rounding_at_image_edges():
    raw='31 0.31959133 0.03753988 0.09069734 0.07507977\n31 0.22386191 0.96275249 0.08801044 0.07449503\n27 0.56967514 0.75024939 0.86064973 0.33559017\n'
    boxes=core.parse_label(raw,37)
    core.validate_boxes(boxes,37)
    assert boxes[0]['y']==0
    assert boxes[1]['y']+boxes[1]['h']==pytest.approx(1)
    assert boxes[2]['x']+boxes[2]['w']==pytest.approx(1)
    assert boxes[0]['h']==pytest.approx(0.075079765)
    with pytest.raises(HTTPException,match='BBox座標が不正です'):
        core.parse_label('31 0.32 0.03 0.09 0.07',37)

def test_save_backup_conflict_status(sample):
    p,editor=sample;d=core.datasets()[0];f=p/'images'/'2.jpg';v=core.label_info(d,p,f)['version']
    old=(p/'labels'/'2.txt').read_bytes();b=[{'class_id':1,'x':.2,'y':.3,'w':.2,'h':.4}]
    result=core.save(d,p,f,b,v)
    assert result['boxes'][0]['class_id']==1
    assert list((editor/'backups').rglob('*.bak'))[0].read_bytes()==old
    assert core.status_for(f)['modified']
    with pytest.raises(HTTPException) as exc:core.save(d,p,f,b,v)
    assert exc.value.status_code==409
    core.set_status(f,'reviewed');assert core.status_for(f)['status']=='reviewed'
    assert core.status_for(f)['status']=='reviewed'

def test_corrupt_and_failure_preserves_source(sample,monkeypatch):
    p,_=sample;d=core.datasets()[0];f=p/'images'/'2.jpg';label=p/'labels'/'2.txt'
    label.write_text('bad\n');v=core.version(label)
    with pytest.raises(HTTPException):core.save(d,p,f,[],v)
    assert label.read_text()=='bad\n'
    label.write_text('0 .5 .5 .2 .4\n');v=core.version(label);old=label.read_bytes()
    def fail(*args):raise OSError('disk full')
    monkeypatch.setattr(core,'atomic_write',fail)
    with pytest.raises(OSError):core.save(d,p,f,[{'class_id':1,'x':.3,'y':.3,'w':.2,'h':.2}],v)
    assert label.read_bytes()==old

def test_paths_and_api(sample,tmp_path):
    p,_=sample;outside=tmp_path/'outside.jpg';outside.write_bytes(b'x')
    (p/'images'/'escape.jpg').symlink_to(outside)
    with pytest.raises(HTTPException):core.safe(outside)
    assert all(x['name']!='escape.jpg' for x in core.images(core.datasets()[0]['id']))
    ds=main.datasets()[0];rows=main.images(ds['id'])
    assert main.image(rows[0]['id'])['name']=='2.jpg'
    with pytest.raises(HTTPException):main.image('not-an-id')
    assert main.health()['ok']

def test_missing_label_and_status_api(sample):
    p,_=sample;ds=core.datasets()[0];rows=core.images(ds['id']);id=rows[1]['id']
    assert main.annotations(id)['boxes']==[]
    assert main.status(id,main.StatusBody(status='held'))['status']=='held'
    assert main.stats(ds['id'])['held']==1
    assert main.save_annotations(id,main.SaveBody(version='missing',boxes=[{'class_id':0,'x':.1,'y':.1,'w':.2,'h':.2}]))['boxes']
    assert (p/'labels'/'10.txt').exists()

def test_standard_split_and_read_only(tmp_path,monkeypatch):
    data=tmp_path/'data';editor=tmp_path/'annotation-editor';root=data/'standard';train=root/'train'
    (train/'images').mkdir(parents=True);(train/'labels').mkdir()
    (root/'data.yaml').write_text('names: [1m]\nnc: 1\n')
    Image.new('RGB',(40,30)).save(train/'images'/'tile.png')
    readonly=data/'unlabeled'/'images';readonly.mkdir(parents=True)
    Image.new('RGB',(40,30)).save(readonly/'view.jpg')
    monkeypatch.setattr(core,'DATA',data);monkeypatch.setattr(core,'EDITOR',editor)
    ds=core.datasets();assert len(ds)==2
    edit=next(d for d in ds if d['editable']);view=next(d for d in ds if not d['editable'])
    assert edit['path']=='standard/train' and core.classes_for(edit,train)==['1m']
    assert core.images(edit['id'])[0]['name']=='tile.png'
    assert core.images(view['id'])[0]['name']=='view.jpg'
