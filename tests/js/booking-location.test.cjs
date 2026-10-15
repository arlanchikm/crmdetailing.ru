const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync('static/js/booking-location.js','utf8');
function harness(ua){
  const listeners={},timers=new Map(),opened=[];let clock=0;
  const route={dataset:{yandexApp:'yandexmaps://route',appleApp:'maps://route',google:'https://google.com/route',android:'intent://route'},addEventListener:(name,fn)=>listeners['route:'+name]=fn};
  const dialog={showModal(){},querySelector(selector){return selector==='iframe'?{}:selector==='.booking-route-button'?route:{addEventListener(){}}},addEventListener:(name,fn)=>listeners['dialog:'+name]=fn};
  const document={hidden:false,getElementById:()=>dialog,querySelectorAll:()=>[],addEventListener:(name,fn)=>listeners[name]=fn,removeEventListener:name=>delete listeners[name]};
  const window={location:{set href(url){opened.push(url)}},addEventListener:(name,fn)=>listeners[name]=fn,removeEventListener:name=>delete listeners[name]};
  vm.runInNewContext(source,{document,window,navigator:{userAgent:ua,maxTouchPoints:0},setTimeout:fn=>{timers.set(++clock,fn);return clock},clearTimeout:id=>timers.delete(id)});
  return {opened,document,listeners,click(){let prevented=false;listeners['route:click']({preventDefault(){prevented=true}});return prevented},tick(){const row=timers.entries().next().value;if(row){timers.delete(row[0]);row[1]()}},timers};
}
{
 const h=harness('iPhone');assert.equal(h.click(),true);h.tick();h.tick();
 assert.deepEqual(h.opened,['yandexmaps://route','maps://route','https://google.com/route']);assert.equal(h.timers.size,0);
}
for(const afterApple of [false,true]){
 const h=harness('iPhone');h.click();if(afterApple)h.tick();h.document.hidden=true;h.listeners.visibilitychange();h.document.hidden=false;h.tick();
 assert.equal(h.opened.length,afterApple?2:1);assert.equal(h.timers.size,0);
}
{
 const h=harness('iPhone');h.click();h.listeners.blur();h.tick();assert.deepEqual(h.opened,['yandexmaps://route']);
 h.click();h.listeners['dialog:close']();h.tick();assert.equal(h.opened.length,2);
}
{
 const h=harness('iPhone');h.click();h.click();assert.equal(h.timers.size,1);h.tick();h.tick();assert.equal(h.opened.filter(url=>url==='https://google.com/route').length,1);
}
{
 const h=harness('Android');assert.equal(h.click(),true);assert.deepEqual(h.opened,['intent://route']);assert.equal(h.timers.size,0);
}
{
 const h=harness('Macintosh');assert.equal(h.click(),false);assert.deepEqual(h.opened,[]);
}
console.log('Route fallback checks passed (order, app launch, OS prompt, repeat click, dialog close, Android, desktop).');
