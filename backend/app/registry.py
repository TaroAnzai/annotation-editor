"""SQLite registry; all database locations are under the configured data root."""
from __future__ import annotations
import json, sqlite3, hashlib, threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from . import core

def now(): return datetime.now(timezone.utc).isoformat()
def sha(path:Path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()
def db_path(): return core.DATA/'registry'/'registry.sqlite'
SCHEMA='''
CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, path TEXT UNIQUE NOT NULL, format TEXT NOT NULL, origin TEXT, provenance TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS images(id INTEGER PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, path TEXT NOT NULL, filename TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, source_type TEXT NOT NULL, origin TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batch_images(batch_id INTEGER NOT NULL REFERENCES batches(id), image_id INTEGER NOT NULL REFERENCES images(id), path TEXT NOT NULL, duplicate INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(batch_id,image_id));
CREATE TABLE IF NOT EXISTS annotations(id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES batches(id), image_id INTEGER NOT NULL REFERENCES images(id), format TEXT NOT NULL, path TEXT, image_key TEXT, content_sha256 TEXT NOT NULL, content_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL, updated_at TEXT NOT NULL, reviewed_at TEXT, UNIQUE(batch_id,image_id));
CREATE TABLE IF NOT EXISTS annotation_history(id INTEGER PRIMARY KEY, annotation_id INTEGER NOT NULL REFERENCES annotations(id), revision INTEGER NOT NULL, content_sha256 TEXT NOT NULL, changed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS status_history(id INTEGER PRIMARY KEY, annotation_id INTEGER NOT NULL REFERENCES annotations(id), old_status TEXT NOT NULL, new_status TEXT NOT NULL, changed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conflicts(id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, image_id INTEGER NOT NULL, other_annotation_id INTEGER NOT NULL, content_sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY, parent_id TEXT, kind TEXT NOT NULL, path TEXT NOT NULL, description TEXT, created_at TEXT NOT NULL, manifest_json TEXT NOT NULL, dataset_hash TEXT, test_id TEXT, status TEXT NOT NULL, upload_status TEXT NOT NULL DEFAULT 'not_uploaded');
CREATE TABLE IF NOT EXISTS version_images(version_id TEXT NOT NULL REFERENCES versions(id), image_id INTEGER NOT NULL REFERENCES images(id), split TEXT NOT NULL, image_sha256 TEXT NOT NULL, annotation_sha256 TEXT NOT NULL, content_json TEXT NOT NULL, source_type TEXT NOT NULL, PRIMARY KEY(version_id,image_id));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, progress INTEGER NOT NULL, total INTEGER NOT NULL, error TEXT, result TEXT, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_annotations_status ON annotations(status);
CREATE INDEX IF NOT EXISTS idx_version_images_split ON version_images(version_id,split);
'''
_initialized=set()
_schema_lock=threading.Lock()
@contextmanager
def connect():
    path=db_path(); path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=30); db.row_factory=sqlite3.Row; db.execute('PRAGMA foreign_keys=ON')
    with _schema_lock:
        if path not in _initialized:
            db.executescript(SCHEMA);_initialized.add(path)
    try: yield db; db.commit()
    except: db.rollback(); raise
    finally: db.close()
def rel(path:Path): return str(core.safe(path).relative_to(core.DATA))
def path(relative:str): return core.safe(core.DATA/relative)
def json_bytes(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def digest(value): return hashlib.sha256(json_bytes(value)).hexdigest()
def box_digest(boxes):
    return digest([{'class_id':b['class_id'],**{k:round(float(b[k]),9) for k in ('x','y','w','h')}} for b in boxes])
def get_status(f:Path):
    if not db_path().exists(): return None
    with connect() as db:
        row=db.execute('SELECT a.status,a.revision,a.content_sha256 FROM annotations a JOIN batch_images bi ON bi.batch_id=a.batch_id AND bi.image_id=a.image_id WHERE bi.path=? LIMIT 1',(rel(f),)).fetchone()
        return {'status':row['status'],'modified':row['revision']>1,'revision':row['revision'],'content_sha256':row['content_sha256']} if row else None

def all_statuses():
    """Fetch editor statuses in one query for dataset listings."""
    if not db_path().exists(): return {}
    with connect() as db:
        return {row['path']:{'status':row['status'],'modified':row['revision']>1}
                for row in db.execute('''SELECT bi.path,a.status,a.revision FROM batch_images bi
                                         JOIN annotations a ON a.batch_id=bi.batch_id AND a.image_id=bi.image_id''')}

def update_status(f:Path,status:str,modified=None):
    if not db_path().exists(): return False
    with connect() as db:
        row=db.execute('SELECT a.id,a.status FROM annotations a JOIN batch_images bi ON bi.batch_id=a.batch_id AND bi.image_id=a.image_id WHERE bi.path=? LIMIT 1',(rel(f),)).fetchone()
        if not row: return False
        db.execute('UPDATE annotations SET status=?,reviewed_at=?,updated_at=? WHERE id=?',(status,now() if status=='reviewed' else None,now(),row['id']))
        if row['status']!=status: db.execute('INSERT INTO status_history(annotation_id,old_status,new_status,changed_at) VALUES(?,?,?,?)',(row['id'],row['status'],status,now()))
    return True

def annotation_saved(f:Path,boxes):
    if not db_path().exists(): return
    with connect() as db:
        row=db.execute('SELECT a.id,a.revision,a.status FROM annotations a JOIN batch_images bi ON bi.batch_id=a.batch_id AND bi.image_id=a.image_id WHERE bi.path=? LIMIT 1',(rel(f),)).fetchone()
        if not row:return
        d=box_digest(boxes); revision=row['revision']+1
        db.execute('UPDATE annotations SET content_json=?,content_sha256=?,revision=?,status=?,reviewed_at=NULL,updated_at=? WHERE id=?',(json.dumps(boxes),d,revision,'unreviewed',now(),row['id']))
        db.execute('INSERT INTO annotation_history(annotation_id,revision,content_sha256,changed_at) VALUES(?,?,?,?)',(row['id'],revision,d,now()))
        if row['status']!='unreviewed': db.execute('INSERT INTO status_history(annotation_id,old_status,new_status,changed_at) VALUES(?,?,?,?)',(row['id'],row['status'],'unreviewed',now()))

def list_batches():
    with connect() as db:
        rows=db.execute('''SELECT b.*, COUNT(DISTINCT bi.image_id) image_count, SUM(bi.duplicate) duplicate_count, SUM(CASE WHEN a.status='reviewed' THEN 1 ELSE 0 END) reviewed_count, SUM(CASE WHEN a.status='held' THEN 1 ELSE 0 END) held_count, SUM(CASE WHEN a.status IN ('unreviewed','provisional','unannotated') THEN 1 ELSE 0 END) unreviewed_count FROM batches b LEFT JOIN batch_images bi ON bi.batch_id=b.id LEFT JOIN annotations a ON a.batch_id=b.id AND a.image_id=bi.image_id GROUP BY b.id ORDER BY b.id DESC''').fetchall()
        result=[]
        for item in rows:
            b=dict(item)
            annotations=db.execute('SELECT content_json FROM annotations WHERE batch_id=?',(b['id'],)).fetchall()
            b['annotation_count']=sum(bool(json.loads(r[0])) for r in annotations)
            b['bbox_count']=sum(len(json.loads(r[0])) for r in annotations)
            b['source_types']={r['source_type']:r['n'] for r in db.execute('SELECT i.source_type,COUNT(*) n FROM batch_images bi JOIN images i ON i.id=bi.image_id WHERE bi.batch_id=? GROUP BY i.source_type',(b['id'],))}
            result.append(b)
        return result

def sync_batch_annotations(batch_id:int,progress=None):
    """Reconcile one registered batch with its current annotation files."""
    from . import importer
    with connect() as db:
        batch=db.execute('SELECT name,path,format FROM batches WHERE id=?',(batch_id,)).fetchone()
        if not batch: raise ValueError('バッチが見つかりません')
        registered={r['path']:dict(r) for r in db.execute('''SELECT bi.path,i.sha256,i.source_type,a.id,a.content_json,a.content_sha256,a.revision,a.status
            FROM batch_images bi JOIN images i ON i.id=bi.image_id
            JOIN annotations a ON a.batch_id=bi.batch_id AND a.image_id=bi.image_id
            WHERE bi.batch_id=?''',(batch_id,))}
    root=path(batch['path'])
    source_types={r['source_type'] for r in registered.values()}
    scan=importer.inspect(root,next(iter(source_types)) if len(source_types)==1 else None)
    if scan['kind']!=batch['format']: raise ValueError('バッチの形式が登録時から変更されています')
    errors=[e for e in scan['errors'] if not e.startswith('manifestの')]
    if errors: raise ValueError('アノテーションの検証に失敗しました: '+str(errors))
    current={rel(Path(r['path'])):r for r in scan['rows']}
    if set(current)!=set(registered): raise ValueError('登録済み画像と現在の画像が一致しません')
    if any(r['sha256']!=registered[key]['sha256'] for key,r in current.items()):
        raise ValueError('登録済み画像と現在の画像の内容が一致しません')
    if progress: progress(1)
    # Guard against edits while files are being scanned and while the DB is written.
    annotation_files={Path(r['annotation_path']) for r in current.values() if r['annotation_path']}
    annotation_files.update(f for f in (root/'data.yaml',root/'dataset.yaml') if f.is_file())
    snapshots={f:sha(f) for f in annotation_files}
    rescanned=importer.inspect(root,next(iter(source_types)) if len(source_types)==1 else None)
    if [e for e in rescanned['errors'] if not e.startswith('manifestの')] or {
        rel(Path(r['path'])):(r['sha256'],r['annotation_path'],box_digest(r['boxes'])) for r in rescanned['rows']
    }!={key:(r['sha256'],r['annotation_path'],box_digest(r['boxes'])) for key,r in current.items()}:
        raise ValueError('同期中にアノテーションファイルが変更されました')
    changed=review_reset=0
    with connect() as db:
        if any(sha(f)!=digest for f,digest in snapshots.items()):
            raise ValueError('同期中にアノテーションファイルが変更されました')
        for key,r in current.items():
            old=registered[key]
            if sha(path(key))!=old['sha256']: raise ValueError(f'同期中に画像が変更されました: {key}')
            latest=db.execute('SELECT content_sha256,revision,status FROM annotations WHERE id=?',(old['id'],)).fetchone()
            if not latest or tuple(latest)!=(old['content_sha256'],old['revision'],old['status']):
                raise ValueError(f'同期中にDBのアノテーションが変更されました: {key}')
            boxes=r['boxes'];digest_now=box_digest(boxes)
            if digest_now==old['content_sha256']: continue
            previous=json.loads(old['content_json'])
            equivalent=(len(previous)==len(boxes) and all(
                a['class_id']==b['class_id'] and all(abs(a[k]-b[k])<=1e-8 for k in ('x','y','w','h'))
                for a,b in zip(previous,boxes)))
            new_status=old['status'] if equivalent else ('unannotated' if not boxes else 'unreviewed')
            timestamp=now();revision=old['revision']+1
            db.execute('''UPDATE annotations SET content_json=?,content_sha256=?,revision=?,status=?,reviewed_at=CASE WHEN ? THEN reviewed_at ELSE NULL END,updated_at=? WHERE id=?''',
                       (json.dumps(boxes),digest_now,revision,new_status,equivalent,timestamp,old['id']))
            db.execute('INSERT INTO annotation_history(annotation_id,revision,content_sha256,changed_at) VALUES(?,?,?,?)',
                       (old['id'],revision,digest_now,timestamp))
            if new_status!=old['status']:
                db.execute('INSERT INTO status_history(annotation_id,old_status,new_status,changed_at) VALUES(?,?,?,?)',
                           (old['id'],old['status'],new_status,timestamp))
            changed+=1
            if old['status']=='reviewed' and new_status!='reviewed': review_reset+=1
        if any(sha(f)!=digest for f,digest in snapshots.items()):
            raise ValueError('同期中にアノテーションファイルが変更されました')
    if progress: progress(2)
    return f"{batch['name']}: DBのアノテーション {changed} 枚を更新、再確認が必要 {review_reset} 枚"

def confirm_batch(batch_id:int,progress=None):
    from . import versions
    rows=versions.rows_from_db([batch_id],require_review=False)
    if not rows or any(not json.loads(r['content_json']) for r in rows): raise ValueError('BBoxのない画像があるため一括確認できません')
    if progress: progress(1)
    with connect() as db:
        for row in rows:
            annotation=db.execute('SELECT id,status,content_sha256 FROM annotations WHERE batch_id=? AND image_id=?',(batch_id,row['image_id'])).fetchone()
            if not annotation or annotation['content_sha256']!=row['content_sha256']:
                raise ValueError(f"確認中にannotationが変更されました: {row['filename']}")
            if annotation['status']=='reviewed': continue
            changed=db.execute("UPDATE annotations SET status='reviewed',reviewed_at=?,updated_at=? WHERE id=? AND content_sha256=? AND status=?",
                               (now(),now(),annotation['id'],row['content_sha256'],annotation['status']))
            if changed.rowcount!=1: raise ValueError(f"確認中にannotationが変更されました: {row['filename']}")
            db.execute('INSERT INTO status_history(annotation_id,old_status,new_status,changed_at) VALUES(?,?,?,?)',(annotation['id'],annotation['status'],'reviewed',now()))
    if progress: progress(2)
    return {'batch_id':batch_id,'reviewed':len(rows)}

def analysis():
    from .schema import TILE_CLASSES
    with connect() as db:
        rows=db.execute('SELECT content_json,status FROM annotations').fetchall()
        by_class=[0]*37; class_images=[0]*37; states={}; count=0
        for row in rows:
            boxes=json.loads(row['content_json']); seen=set()
            for b in boxes:
                c=b['class_id']; by_class[c]+=1;seen.add(c);count+=1
            for c in seen:class_images[c]+=1
            states[row['status']]=states.get(row['status'],0)+1
        types={r['source_type']:r['n'] for r in db.execute('SELECT source_type,COUNT(*) n FROM images GROUP BY source_type')}
        latest=db.execute("SELECT id,manifest_json FROM versions WHERE kind='completed' ORDER BY created_at DESC LIMIT 1").fetchone()
        split_counts={s:json.loads(latest['manifest_json'])['splits'][s]['image_count'] for s in ('train','val','test')} if latest else None
        return {'images':db.execute('SELECT COUNT(*) FROM images').fetchone()[0],'bbox_count':count,'bbox_by_class':by_class,'images_by_class':class_images,'classes':TILE_CLASSES,'statuses':states,'source_types':types,'latest_version':latest['id'] if latest else None,'split_counts':split_counts}
