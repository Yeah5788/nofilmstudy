(function(){var DATA={},cbs=[];function get(){return fetch("data.json?t="+Date.now()).then(function(r){return r.json()}).then(function(d){DATA=d;cbs.forEach(function(f){try{f()}catch(e){}})})}
var ready=get();setInterval(get,5*60e3);
function snap(c){return {docs:Object.entries(DATA[c]||{}).map(function(e){return {id:e[0],data:function(){return e[1]},exists:true}})}}
function coll(c){var q={orderBy:function(){return q},limit:function(){return q},where:function(){return q},onSnapshot:function(fn){var f=function(){fn(snap(c))};cbs.push(f);ready.then(f);return function(){}}};return q}
var deny=function(){return Promise.reject({code:"permission-denied"})};
var db={collection:coll,doc:function(p){var a=p.split("/"),c=a.slice(0,-1).join("/"),id=a[a.length-1];return {onSnapshot:function(fn){var f=function(){var v=DATA[c]&&DATA[c][id];fn({exists:!!v,data:function(){return v}})};cbs.push(f);ready.then(f);return function(){}},set:deny,update:deny,delete:deny}}};
window.claude={use:function(n){return Promise.resolve(n==="db"?db:null)}};window.PUBLIC_SITE=true})();
