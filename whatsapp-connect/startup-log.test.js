const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
test('startup listener logs no secret-bearing base path', () => {
  const source = fs.readFileSync(__dirname + '/server.js', 'utf8');
  const startup = source.match(/app\.listen\(PORT,[\s\S]*?\n\);/)[0];
  const logs = [];
  const BASE = '/connect-whatsapp/synthetic-private-token';
  vm.runInNewContext(startup, {PORT:8085, BASE, log:{info:(fmt,...args)=>{logs.push(fmt.replace(/%d/,()=>args.shift()))},
    console:{log:value=>logs.push(value)}},
    app:{listen:(port, host, callback)=>{assert.equal(port,8085);assert.equal(host,'127.0.0.1');callback();}}});
  assert.deepEqual(logs, ['whatsapp-connect listening on 127.0.0.1:8085']);
  assert.equal(logs.join().includes(BASE), false);
  assert.equal(logs.join().includes('synthetic-private-token'), false);
});
