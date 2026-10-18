from fastapi import FastAPI, HTTPException
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from fastapi.responses import FileResponse
from pydantic import BaseModel
from . import core, editor_read, importer, registry, versions, export, folder_picker, vps
from PIL import Image

app=FastAPI(title='Mahjong Annotation Editor')
class SaveBody(BaseModel):
    boxes:list[dict]
    version:str
class StatusBody(BaseModel):
    status:str
class ImportBody(BaseModel):
    path:str
    mode:str='copy'
    source_type:str|None=None
    name:str=''
    preview_id:str|None=None
    origin:str=''
class FolderPickerBody(BaseModel):
    current_path:str=''
class VpsConnectionBody(BaseModel):
    host:str
    remote_root:str
class VpsCopyBody(VpsConnectionBody):
    version:str
class VersionBody(BaseModel):
    parent:str|None=None
    batch_ids:list[int]|None=None
    image_ids:list[int]|None=None
    test_real:int=0
    test_synthetic:int=0
    val_ratio:float=.1
    seed:int=42
    keep_test:bool=True
    description:str=''
class ReferenceVersionBody(BaseModel):
    parent:str
    reference_path:str
    description:str=''
pool=ThreadPoolExecutor(max_workers=2)
def job_result_text(kind,result):
    if kind in ('version','unpartitioned','reference') and isinstance(result,dict) and result.get('id'):
        manifest=result.get('manifest') or {}
        splits=manifest.get('splits') or result.get('counts') or {}
        if splits:
            counts={name:value.get('image_count',0) if isinstance(value,dict) else value for name,value in splits.items()}
            return f"{result['id']} を作成しました（train {counts.get('train',0)} / val {counts.get('val',0)} / test {counts.get('test',0)} 枚）"
        if 'image_count' in result: return f"{result['id']} を作成しました（画像 {result['image_count']} 枚）"
        return f"{result['id']} を作成しました"
    return str(result)
def job(kind,fn,*args,**kwargs):
    key=uuid4().hex
    total={'import':3,'import_version':3,'version':6,'reference':6,'unpartitioned':3,'confirm_batch':2,'sync_batch':2,'archive':2,'upload':3,'vps_import':5}.get(kind,1)
    with registry.connect() as db: db.execute('INSERT INTO jobs(id,kind,status,progress,total,updated_at) VALUES(?,?,?,?,?,?)',(key,kind,'queued',0,total,registry.now()))
    def run():
        def progress(n):
            with registry.connect() as db: db.execute('UPDATE jobs SET progress=?,updated_at=? WHERE id=?',(n,registry.now(),key))
        with registry.connect() as db: db.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?",(registry.now(),key))
        try:
            result=fn(*args,**kwargs,progress=progress)
            with registry.connect() as db: db.execute("UPDATE jobs SET status='complete',progress=?,result=?,updated_at=? WHERE id=?",(total,job_result_text(kind,result),registry.now(),key))
        except Exception as e:
            with registry.connect() as db: db.execute("UPDATE jobs SET status='failed',error=?,updated_at=? WHERE id=?",(str(e),registry.now(),key))
    pool.submit(run)
    return {'job_id':key}

@app.get('/api/health')
def health(): return {'ok':True}
@app.get('/api/folders')
def folders(): return core.folders()
@app.get('/api/datasets')
def datasets(): return core.datasets()
@app.get('/api/datasets/{dataset_id}/images')
def images(dataset_id:str): return core.images(dataset_id)
@app.get('/api/datasets/{dataset_id}/stats')
def stats(dataset_id:str):
    items=core.images(dataset_id)
    return {'total':len(items),'reviewed':sum(x['status']=='reviewed' for x in items),'held':sum(x['status']=='held' for x in items),'unreviewed':sum(x['status'] in ('unreviewed','provisional','unannotated') for x in items),'modified':sum(x['modified'] for x in items)}
@app.get('/api/editor/overview')
def editor_overview(): return editor_read.overview()
@app.get('/api/editor/datasets/{dataset_id}/images')
def editor_images(dataset_id:str): return editor_read.list_images(dataset_id)
def image_detail(d,p,f,image_id):
    try:
        with Image.open(f) as im: width,height=im.size; im.verify()
    except Exception as e: raise HTTPException(422,f'画像読み込みエラー: {e}')
    return {'id':image_id,'name':f.name,'width':width,'height':height,'editable':d['editable'],'classes':core.classes_for(d,p) if d['editable'] or d.get('format')=='coco' else [],'status':core.status_for(f),'annotations':core.label_info(d,p,f)}
@app.get('/api/editor/images/{image_id}')
def editor_image(image_id:str):
    return image_detail(*editor_read.get_image(image_id),image_id)
@app.get('/api/editor/images/{image_id}/file')
def editor_image_file(image_id:str):
    _,_,f=editor_read.get_image(image_id)
    return FileResponse(f)
@app.get('/api/images/{image_id}')
def image(image_id:str):
    return image_detail(*core.image(image_id),image_id)
@app.get('/api/images/{image_id}/file')
def image_file(image_id:str):
    _,_,f=core.image(image_id)
    return FileResponse(f)
@app.get('/api/images/{image_id}/annotations')
def annotations(image_id:str):
    d,p,f=core.image(image_id);return core.label_info(d,p,f)
@app.put('/api/images/{image_id}/annotations')
def save_annotations(image_id:str,body:SaveBody):
    d,p,f=core.image(image_id);result=core.save(d,p,f,body.boxes,body.version);registry.annotation_saved(f,result['boxes']);return result
@app.put('/api/images/{image_id}/status')
def status(image_id:str,body:StatusBody):
    d,_,f=core.image(image_id)
    if not d['editable']: raise HTTPException(403,'閲覧専用です')
    return core.set_status(f,body.status)

@app.get('/api/manage/batches')
def batches(): return registry.list_batches()
@app.post('/api/manage/batches/{batch_id}/confirm')
def batch_confirm(batch_id:int): return job('confirm_batch',registry.confirm_batch,batch_id)
@app.post('/api/manage/batches/{batch_id}/sync')
def batch_sync(batch_id:int): return job('sync_batch',registry.sync_batch_annotations,batch_id)
@app.get('/api/manage/analysis')
def analysis(): return registry.analysis()
@app.get('/api/manage/vps/defaults')
def vps_defaults(): return vps.defaults()
@app.post('/api/manage/vps/versions')
def vps_versions(body:VpsConnectionBody):
    try: return {'versions':vps.list_versions(body.host,body.remote_root)}
    except (OSError,ValueError) as e: raise HTTPException(422,str(e))
@app.post('/api/manage/vps/copy')
def vps_copy(body:VpsCopyBody):
    try:
        vps.validate_connection(body.host,body.remote_root)
        if not vps.VERSION_PATTERN.fullmatch(body.version): raise ValueError('不正な版名です')
    except ValueError as e: raise HTTPException(422,str(e))
    return job('vps_import',vps.import_version,body.host,body.remote_root,body.version)
@app.post('/api/manage/import/select-folder')
def select_import_folder(body:FolderPickerBody):
    try: return {'path':folder_picker.choose_folder(body.current_path)}
    except RuntimeError as e: raise HTTPException(503,str(e))
@app.post('/api/manage/import/preview')
def import_preview(body:ImportBody):
    try:return importer.preview(body.path,body.mode,body.source_type)
    except Exception as e:raise HTTPException(422,str(e))
@app.post('/api/manage/import')
def import_execute(body:ImportBody):
    if not body.preview_id: raise HTTPException(422,'プレビューが必要です')
    if importer.layout(importer.source_path(body.path,body.mode)) in ('merged','dataset'):
        return job('import_version',versions.import_version,body.path,body.mode,body.name,body.source_type,body.preview_id)
    return job('import',importer.register,body.path,body.mode,body.source_type,body.name,body.preview_id,body.origin)
@app.get('/api/manage/versions')
def version_list(): return versions.versions()
@app.post('/api/manage/versions')
def version_create(body:VersionBody): return job('version',versions.create,**body.model_dump())
@app.post('/api/manage/versions/unpartitioned')
def version_unpartitioned(body:VersionBody): return job('unpartitioned',versions.create_unpartitioned,body.batch_ids,body.description)
@app.post('/api/manage/versions/from-reference')
def version_from_reference(body:ReferenceVersionBody):
    return job('reference',versions.create_from_reference,body.parent,body.reference_path,body.description)
@app.get('/api/manage/versions/{version_id}/validate')
def version_validate(version_id:str):
    try:
        with registry.connect() as db: row=db.execute('SELECT path FROM versions WHERE id=?',(version_id,)).fetchone()
        if not row: raise ValueError('バージョンがありません')
        return versions.validate(registry.path(row[0]))
    except Exception as e:raise HTTPException(422,str(e))
@app.post('/api/manage/versions/{version_id}/archive')
def version_archive(version_id:str): return job('archive',export.archive,version_id)
@app.get('/api/manage/drive/setup')
def drive_setup(): return export.setup()
@app.post('/api/manage/versions/{version_id}/upload')
def version_upload(version_id:str,execute:bool=False): return job('upload',export.upload,version_id,execute)
@app.get('/api/manage/jobs/{job_id}')
def job_status(job_id:str):
    with registry.connect() as db: row=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not row:raise HTTPException(404,'処理がありません')
    return dict(row)
