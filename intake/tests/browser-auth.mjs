import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
export const password='Synthetic browser password 2026!';
export function provision(repo,workspace,username='admin',name='Synthetic operator',role='admin',secret=password){
  const dir=path.join(repo,'intake/.local',workspace);fs.mkdirSync(dir,{recursive:true,mode:0o700});
  const file=path.join(dir,`test-password-${username}.txt`);fs.writeFileSync(file,secret,{mode:0o600,flag:'wx'});
  try{const result=spawnSync('python3',['-m','intake','--workspace',workspace,'user-set','--username',username,'--name',name,'--role',role,'--password-file',file],{cwd:repo,encoding:'utf8',timeout:15000});assert.equal(result.status,0,'Test account provisioning failed');return JSON.parse(result.stdout);}
  finally{fs.unlinkSync(file);}
}
export async function signIn(page,origin,username='admin',next='/inbox/',secret=password){
  await page.goto(origin+'/login?next='+encodeURIComponent(next));
  await page.locator('#username').fill(username);await page.locator('#password').fill(secret);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await page.waitForURL(origin+next);await page.locator('#signed-in-user').waitFor();
}
export async function api(page,path,data){return page.evaluate(async({path,data})=>{const s=await (await fetch('/api/session')).json();const r=await fetch(path,{method:data?'POST':'GET',headers:{'X-Intake-Token':s.token,'Content-Type':'application/json'},...(data?{body:JSON.stringify(data)}:{})});return {status:r.status,body:await r.json()};},{path,data});}
