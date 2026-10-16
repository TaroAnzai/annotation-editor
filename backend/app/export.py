"""ZIP snapshots and explicit rclone upload."""
from __future__ import annotations
import json, os, shutil, subprocess, tempfile, zipfile, hashlib
from pathlib import Path
from . import core, registry as reg, versions

REMOTE='gdrive:MahjongTileAI/dataset'
def archive(version_id,publish_latest=True,progress=None):
    with reg.connect() as db: row=db.execute('SELECT * FROM versions WHERE id=?',(version_id,)).fetchone()
    if not row or row['kind']!='completed': raise ValueError('完成バージョンだけをZIP化できます')
    root=reg.path(row['path']);summary=versions.validate(root)
    if progress: progress(1)
    exports=core.DATA/'exports';exports.mkdir(parents=True,exist_ok=True)
    from datetime import datetime
    stamp=datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')
    suffix=0
    while True:
        snap=stamp+(f'-{suffix:02d}' if suffix else '')
        counts=summary['counts'];name=f"mahjong_dataset_{snap}_train{counts['train']}_val{counts['val']}_test{counts['test']}.zip"
        target=exports/name
        if not target.exists(): break
        suffix+=1
    fd,tmp=tempfile.mkstemp(prefix='.zip-',dir=exports);os.close(fd)
    try:
        with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=True) as z:
            for p in [root/'dataset_manifest.json',*(q for split in versions.SPLITS for q in sorted((root/split).rglob('*')) if q.is_file())]:
                if p.is_file() and not p.is_symlink(): z.write(p,p.relative_to(root).as_posix())
        with zipfile.ZipFile(tmp) as z:
            if any(x not in z.namelist() for x in ('dataset_manifest.json','train/labels/instances.json','val/labels/instances.json','test/labels/instances.json')): raise ValueError('ZIP構成が不正です')
        os.link(tmp,target)  # Atomic create; never replace an older snapshot.
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    metadata={'snapshot_id':snap,'file':name,'created_at':reg.now(),'train_count':counts['train'],'val_count':counts['val'],'test_count':counts['test'],'total_count':sum(counts.values()),'sha256':reg.sha(target),'dataset_id':summary['dataset_id'],'dataset_manifest_sha256':'sha256:'+reg.sha(root/'dataset_manifest.json')}
    if publish_latest: core.atomic_write(exports/'latest.json',(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n').encode())
    if progress: progress(2)
    return metadata

def setup():
    binary=shutil.which('rclone')
    if not binary:return {'ready':False,'message':'rcloneをインストールし、rclone configでgdrive remoteを設定してください'}
    result=subprocess.run([binary,'listremotes'],capture_output=True,text=True,timeout=15)
    return {'ready':result.returncode==0 and 'gdrive:' in result.stdout.splitlines(),'message':'gdrive remoteをrclone configで設定してください' if 'gdrive:' not in result.stdout.splitlines() else '設定済み'}
def upload(version_id,execute=False,progress=None):
    if not execute:return {'dry_run':True,'remote':REMOTE,'message':'実アップロードにはGUIで明示操作が必要です'}
    state=setup()
    if not state['ready']: raise ValueError(state['message'])
    with reg.connect() as db: row=db.execute('SELECT * FROM versions WHERE id=?',(version_id,)).fetchone()
    if not row or row['kind']!='completed': raise ValueError('完成バージョンだけをアップロードできます')
    current=archive(version_id,False);p=core.DATA/'exports'/current['file'];binary=shutil.which('rclone')
    if progress: progress(1)
    def run(args):
        result=subprocess.run([binary,*args],capture_output=True,text=True,timeout=3600)
        if result.returncode: raise ValueError(result.stderr[-1500:])
        return result.stdout
    run(['copyto',str(p),f"{REMOTE}/{p.name}",'--immutable'])
    if progress: progress(2)
    stat=json.loads(run(['lsjson',f"{REMOTE}/{p.name}",'--stat','--hash']))
    if int(stat.get('Size',-1))!=p.stat().st_size: raise ValueError('アップロード済みZIPのサイズが異なります')
    hashes=stat.get('Hashes',{})
    if hashes.get('SHA-256'):
        if hashes['SHA-256'].lower()!=current['sha256']: raise ValueError('アップロード済みZIPのSHA-256が異なります')
    elif hashes.get('MD5'):
        md5=hashlib.md5()
        with p.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''): md5.update(chunk)
        if hashes['MD5'].lower()!=md5.hexdigest(): raise ValueError('アップロード済みZIPのMD5が異なります')
    else:
        fd,check=tempfile.mkstemp(prefix='.remote-check-',dir=core.DATA/'exports');os.close(fd)
        try:
            run(['copyto',f'{REMOTE}/{p.name}',check])
            if reg.sha(Path(check))!=current['sha256']: raise ValueError('アップロード済みZIPのSHA-256が異なります')
        finally: os.unlink(check)
    fd,temp=tempfile.mkstemp(prefix='.latest-',suffix='.json',dir=core.DATA/'exports');os.close(fd)
    try:
        versions.write_json(Path(temp),current)
        run(['copyto',temp,f'{REMOTE}/latest.json'])
        os.replace(temp,core.DATA/'exports'/'latest.json')
    finally:
        if os.path.exists(temp): os.unlink(temp)
    with reg.connect() as db: db.execute("UPDATE versions SET upload_status='uploaded' WHERE id=?",(version_id,))
    return {'uploaded':True,'metadata':current}
