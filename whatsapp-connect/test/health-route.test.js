const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {createSendAuth}=require('../security');
test('health route authenticates and never sends or exposes the destination',()=>{
 const source=fs.readFileSync(__dirname+'/../server.js','utf8');
 const route=source.match(/app\.get\(`\$\{BASE\}\/send\/check`,[\s\S]*?\n\}\)\);/);
 assert(route,'authenticated non-sending route must be registered');
 let captured;
 const secret='synthetic-send-secret-at-least-32-bytes';
 const context={BASE:'/connect-whatsapp/synthetic-route',state:'connected',
   requireSendAuth:createSendAuth(secret,()=>{}),destJid:()=> 'private-destination',
   sendAlert:()=>{throw Error('Health check must never send');},
   app:{get:(path,auth,handler)=>{captured={path,auth,handler};}}};
 vm.runInNewContext(route[0],context);
 assert.equal(captured.path,'/connect-whatsapp/synthetic-route/send/check');
 for(const [token,status] of [['Bearer wrong',401],['Bearer '+secret,200]]){
  const req={headers:{authorization:token}};
  const res={code:200,set(){return this;},status(code){this.code=code;return this;},json(body){this.body=body;return this;}};
  captured.auth(req,res,()=>captured.handler(req,res));
  assert.equal(res.code,status);
  assert(!JSON.stringify(res.body).includes(secret));
  assert(!JSON.stringify(res.body).includes('private-destination'));
  if(status===200){assert.equal(res.body.route,'owner_alert');assert.equal(res.body.connected,true);assert.equal(res.body.destinationConfigured,true);assert.equal(res.body.delivered,undefined);}
 }
});
