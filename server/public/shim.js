/* 사내 서버판 연결부: 화면 코드가 쓰는 저장소 함수(window.claude.use)를 이 서버로 연결합니다. */
(function(){
"use strict";
window.__YIELD_SERVER__=true;
var SK="kf-yield-manual-v1-session";
function tok(){try{return (JSON.parse(localStorage.getItem(SK))||{}).token||""}catch(e){return ""}}
var cache={},subs=[],opened=0;
function clone(d){return d==null?d:JSON.parse(JSON.stringify(d))}
function snap(id,d){return {id:id,exists:d!=null,data:function(){return d==null?undefined:clone(d)},metadata:{fromCache:false,hasPendingWrites:false}}}
function deliver(s){
  if(s.type==="doc"){s.cb(snap(s.key.split("/")[1],cache[s.key]));return}
  var docs=Object.keys(cache).filter(function(p){var a=p.split("/");return a.length===2&&a[0]===s.key&&(!s.filter||s.filter(cache[p]))}).map(function(p){return snap(p.split("/")[1],cache[p])});
  s.cb({docs:docs,size:docs.length,empty:!docs.length,docChanges:function(){return []},metadata:{fromCache:false,hasPendingWrites:false}})}
function notifyPath(path){var c=path.split("/")[0];subs.forEach(function(s){if((s.type==="doc"&&s.key===path)||(s.type==="coll"&&s.key===c))try{deliver(s)}catch(e){console.error(e)}})}
function notifyAll(){subs.forEach(function(s){try{deliver(s)}catch(e){console.error(e)}})}
function api(method,url,body){
  return fetch(url,{method:method,headers:{"Content-Type":"application/json","Authorization":"Bearer "+tok()},body:body===undefined?undefined:JSON.stringify(body)})
  .then(function(r){return r.json().catch(function(){return {}}).then(function(j){
    if(r.status===401&&tok()){localStorage.removeItem(SK);try{localStorage.setItem("kf-yield-manual-v1-msg",JSON.stringify("로그인이 끝나 다시 로그인해 주세요"))}catch(e){}location.reload();throw {code:"unauthenticated"}}
    if(!r.ok)throw {code:r.status===403?"invalid_argument":(j.code||"http_"+r.status),message:j.error||""};return j})})}
function loadAll(){return api("GET","/api/all").then(function(j){cache=j.docs||{};notifyAll()})}
function startEvents(){
  var es=new EventSource("/api/events?token="+encodeURIComponent(tok()));
  es.onopen=function(){opened++;if(opened>1)loadAll().catch(function(){})};
  es.onmessage=function(e){try{var m=JSON.parse(e.data);if(m.type==="change"){if(m.data==null)delete cache[m.path];else cache[m.path]=m.data;notifyPath(m.path)}}catch(x){}};
}
if(tok())window.__YIELD_PUBLIC__={setupNeeded:false,allowRegister:true};
var ready=(tok()?loadAll().then(startEvents):fetch("/api/public").then(function(r){return r.json()}).then(function(j){
  cache={};if(j.config)cache["config/main"]=j.config;window.__YIELD_PUBLIC__={setupNeeded:!!j.setupNeeded,allowRegister:j.allowRegister!==false};notifyAll()})).catch(function(e){console.error("서버 연결 실패",e)});
function docRef(path){return {id:path.split("/").pop(),path:path,
  get:function(){return ready.then(function(){return snap(path.split("/")[1],cache[path])})},
  set:function(d){return api("PUT","/api/doc/"+path,d).then(function(){cache[path]=clone(d);notifyPath(path)})},
  update:function(d){var m=Object.assign({},cache[path]||{},d);return this.set(m)},
  delete:function(){return api("DELETE","/api/doc/"+path).then(function(){delete cache[path];notifyPath(path)})},
  onSnapshot:function(cb){var s={type:"doc",key:path,cb:cb};subs.push(s);ready.then(function(){deliver(s)});return function(){subs=subs.filter(function(x){return x!==s})}}}}
function collRef(c,filter){return {path:c,
  where:function(f,op,v){var prev=filter;return collRef(c,function(d){var x=d[f],ok=op===">="?x>=v:op==="<="?x<=v:op===">"?x>v:op==="<"?x<v:op==="=="?x===v:op==="!="?x!==v:true;return ok&&(!prev||prev(d))})},
  orderBy:function(){return this},limit:function(){return this},
  doc:function(id){return docRef(c+"/"+(id||("x"+Date.now().toString(36)+Math.random().toString(36).slice(2,8))))},
  onSnapshot:function(cb){var s={type:"coll",key:c,filter:filter,cb:cb};subs.push(s);ready.then(function(){deliver(s)});return function(){subs=subs.filter(function(x){return x!==s})}}}}
var db={doc:docRef,collection:function(c){return collRef(c)}};
var downloads={save:function(o){var blob=o.data instanceof Blob?o.data:new Blob([o.data]);var a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download=o.filename;document.body.appendChild(a);a.click();setTimeout(function(){URL.revokeObjectURL(a.href);a.remove()},1500);return Promise.resolve()}};
var user={canEdit:function(){return Promise.resolve(true)},isOwner:function(){return Promise.resolve(false)}};
window.claude={use:function(n){if(n==="db")return ready.then(function(){return db});if(n==="downloads")return Promise.resolve(downloads);if(n==="user")return Promise.resolve(user);return Promise.resolve(null)}};
})();
