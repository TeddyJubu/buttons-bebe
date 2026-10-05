import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export async function verifyRecovery(page,{cli,artifacts,workspace,repo}){
  await page.getByRole('button',{name:/Photo of a damaged button/}).click();
  await page.getByText('button-photo.jpg · Metadata only · File not downloaded',{exact:true}).waitFor();
  const index=cli(['attachment-index']);
  const records=JSON.parse(fs.readFileSync(index.private_index,'utf8')).attachments;
  const selected=records.find(row=>JSON.parse(row.metadata_json).name==='button-photo.jpg');
  assert(selected);
  const bundle=path.join(artifacts,'synthetic-attachment-bundle');
  fs.mkdirSync(bundle,{mode:0o700});
  const bytes=Buffer.alloc(42000);
  bytes.write('<script>fetch("https://never-execute.example.test/")</script>');
  fs.writeFileSync(path.join(bundle,'payload.bin'),bytes,{mode:0o600});
  fs.writeFileSync(path.join(bundle,'manifest.json'),JSON.stringify({mode:'offline_attachment_import',files:[{
    attachment_id:selected.attachment_id,file:'payload.bin',sha256:createHash('sha256').update(bytes).digest('hex')
  }]}),{mode:0o600});
  const preview=cli(['attachment-preview',bundle]);
  assert.equal(preview.new_links,1);
  assert(await page.getByText('button-photo.jpg · Metadata only · File not downloaded',{exact:true}).isVisible());
  cli(['attachment-import',bundle,'--expected-digest',preview.digest]);
  assert.equal(cli(['attachment-import',bundle,'--expected-digest',preview.digest]).alreadyImported,true);
  await page.locator('#refresh').click();
  await page.getByText('button-photo.jpg · Private copy saved · 42000 bytes · Not opened',{exact:true}).waitFor();
  assert.equal(await page.locator('img,iframe,object').count(),0);
  const backup=cli(['backup','--name','browser-checkpoint']);
  assert.equal(backup.unresolved_attempts,1);
  const verified=cli(['backup-verify','--name','browser-checkpoint']);
  assert.equal(verified.digest,backup.digest);
  const restoredName=workspace+'-restored';
  const restored=cli(['restore','--from-workspace',workspace,'--backup','browser-checkpoint','--expected-digest',verified.digest],restoredName);
  assert.equal(restored.unresolved_attempts,1);
  assert.equal(restored.old_unconfirmed_reviews_invalidated,true);
  const integrity=cli(['verify-workspace'],restoredName);
  assert.equal(integrity.attachment_bytes,42000);
  assert.equal(integrity.unresolved_attempts,1);
  cli(['attachment-copy','--attachment-id',selected.attachment_id,'--name','recovered.bin'],restoredName);
  assert.deepEqual(fs.readFileSync(path.join(repo,'intake/.local',restoredName,'copies/recovered.bin')),bytes);
  const refused=await page.evaluate(async()=>{
    const {token}=await (await fetch('/api/session')).json();
    return Promise.all(['/api/attachments/anything','/backups/browser-checkpoint/intake.sqlite3','/copies/recovered.bin'].map(async url=>(await fetch(url,{headers:{'X-Intake-Token':token}})).status));
  });
  assert.deepEqual(refused,[404,404,404]);
}
