self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
self.addEventListener('push',event=>{
 event.waitUntil((async()=>{
  let data;try{data=event.data.json();}catch{return;}
  const clients=await self.clients.matchAll({type:'window',includeUncontrolled:true});
  const visible=clients.filter(client=>client.visibilityState==='visible');
  if(visible.length){visible.forEach(client=>client.postMessage({type:'crm-notification',data}));return;}
  await self.registration.showNotification(data.title||'CRM Detailing',{
   body:data.message||'',icon:'/static/img/crm-icon-192-20261007.png',badge:'/static/img/crm-icon-192-20261007.png',
   tag:'crm-'+data.id,data,requireInteraction:data.severity==='critical'});
 })());
});
self.addEventListener('notificationclick',event=>{
 event.notification.close();
 event.waitUntil((async()=>{
  const data=event.notification.data||{};
  const url=new URL(data.url||'/notifications/',self.location.origin);
  if(url.origin!==self.location.origin)url.href=self.location.origin+'/notifications/';
  if(data.read_url?.startsWith('/notifications/')){
   // Reading is recorded by the target page after authenticated navigation.
   url.searchParams.set('notification',data.id||'');
  }
  const clients=await self.clients.matchAll({type:'window',includeUncontrolled:true});
  for(const client of clients)if(new URL(client.url).origin===url.origin){await client.navigate(url.href);await client.focus();return;}
  await self.clients.openWindow(url.href);
 })());
});
