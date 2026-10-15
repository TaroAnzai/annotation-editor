from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from . import core
from PIL import Image

app=FastAPI(title='Mahjong Annotation Editor')
class SaveBody(BaseModel):
    boxes:list[dict]
    version:str
class StatusBody(BaseModel):
    status:str

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
    return {'total':len(items),'reviewed':sum(x['status']=='reviewed' for x in items),'held':sum(x['status']=='held' for x in items),'unreviewed':sum(x['status']=='unreviewed' for x in items),'modified':sum(x['modified'] for x in items)}
@app.get('/api/images/{image_id}')
def image(image_id:str):
    d,p,f=core.image(image_id)
    try:
        with Image.open(f) as im: width,height=im.size; im.verify()
    except Exception as e: raise HTTPException(422,f'画像読み込みエラー: {e}')
    return {'id':image_id,'name':f.name,'width':width,'height':height,'editable':d['editable'],'classes':core.classes_for(d,p) if d['editable'] else [],'status':core.status_for(f),'annotations':core.label_info(d,p,f)}
@app.get('/api/images/{image_id}/file')
def image_file(image_id:str):
    _,_,f=core.image(image_id)
    return FileResponse(f)
@app.get('/api/images/{image_id}/annotations')
def annotations(image_id:str):
    d,p,f=core.image(image_id);return core.label_info(d,p,f)
@app.put('/api/images/{image_id}/annotations')
def save_annotations(image_id:str,body:SaveBody):
    d,p,f=core.image(image_id);return core.save(d,p,f,body.boxes,body.version)
@app.put('/api/images/{image_id}/status')
def status(image_id:str,body:StatusBody):
    _,_,f=core.image(image_id);return core.set_status(f,body.status)
