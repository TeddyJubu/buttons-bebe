// Session credentials stay in an HttpOnly, SameSite cookie. CSRF token is page memory only.
let token='',session=null,ending=false;
const channel=new BroadcastChannel('intake-session');
function endSession(){
  if(ending)return;ending=true;
  window.dispatchEvent(new Event('intake-session-ended'));
  document.body.hidden=true;document.body.inert=true;
  location.replace('/login?next='+encodeURIComponent(location.pathname+location.search));
}
channel.onmessage=()=>endSession();
export async function request(path,data){
  if(!/^\/api\/(session|team|auth\/(?:logout|password)|imports|import\/(?:preview|commit)|tickets(?:\?[^#]*|\/[a-zA-Z0-9-]+(?:\/(?:notes|update|claim|read-state|assistance-input|assistance-run|assistance-use|assistance-dismiss|simulation-review|simulation-confirm|simulation-reconcile))?)?)$/.test(path))throw new Error('This action is unavailable in the offline workspace.');
  let response;
  try{response=await fetch(path,{method:data?'POST':'GET',cache:'no-store',credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(20000),headers:{'X-Intake-Token':token,...(data?{'Content-Type':'application/json'}:{})},...(data?{body:JSON.stringify(data)}:{})});}
  catch{throw new Error(data?'The result was not received. Refresh to inspect the record; do not assume it failed.':'The offline workspace could not be reached. Try Refresh.');}
  const body=await response.json().catch(()=>null);
  if(response.status===401)endSession();
  if(!response.ok||body===null){const error=new Error(body?.error||'The workspace returned an unreadable response. Refresh to inspect its state.');error.status=response.status;throw error;}
  return body;
}
export async function start(){
  session=await request('/api/session');const c=session.capabilities;
  if(c?.mode!=='offline_sandbox'||['liveIntake','sendReply','providerReads','aiGeneration','notifications'].some(key=>c[key]!==false)||typeof session.token!=='string'||!session.token||!session.user)throw new Error('This page requires the isolated offline ticket API.');
  token=session.token;setTimeout(endSession,Math.max(0,session.expires_at*1000-Date.now()));return session;
}
export function canWork(){return !!session?.permissions.includes('work');}
export function accountBar(parent){
  const bar=document.createElement('div');bar.className='account-bar';
  const name=document.createElement('span');name.id='signed-in-user';name.textContent=session.user.name+' · '+session.user.role;
  const password=document.createElement('a');password.href='/login?password=1';password.textContent='Password';
  const logout=document.createElement('button');logout.id='sign-out';logout.textContent='Sign out';
  logout.addEventListener('click',async()=>{logout.disabled=true;try{await request('/api/auth/logout',{});channel.postMessage('ended');endSession();}catch(error){logout.disabled=false;logout.textContent=error.message;}});
  bar.append(name,password,logout);parent.append(bar);
}
