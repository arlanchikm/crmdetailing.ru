(()=>{
 document.addEventListener('DOMContentLoaded',()=>{
  const body=document.body;if(!body.dataset.notificationFeed)return;
  const en=body.dataset.uiLanguage==='en',user=body.dataset.userId;
  let cursor=body.dataset.notificationsSince,cursorId="",polling=false,toasts=body.dataset.notificationToasts==='1';
  const seenKey='crm-notifications-seen:'+user;
  let seen;try{seen=new Set(JSON.parse(localStorage.getItem(seenKey)||'[]'));}catch{seen=new Set();}
  const csrf=()=>document.querySelector('input[name="csrfmiddlewaretoken"]')?.value||'';
  const post=(url,data)=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf(),'X-Requested-With':'XMLHttpRequest'},body:JSON.stringify(data)});
  const badge=count=>document.querySelectorAll('[data-notification-count]').forEach(el=>{el.textContent=count;el.hidden=!count;});
  const tray=document.createElement('div');tray.className='notification-toast-tray';tray.setAttribute('aria-live','polite');body.append(tray);
  function show(item){
   if(seen.has(item.id))return;seen.add(item.id);
   while(seen.size>200)seen.delete(seen.values().next().value);
   try{localStorage.setItem(seenKey,JSON.stringify([...seen].slice(-200)));}catch{}
   if(!toasts)return;
   const toast=document.createElement('div');toast.className='notification-toast '+item.severity;
   const link=document.createElement('a');link.href=item.url.startsWith('/')&&!item.url.startsWith('//')?item.url:'/notifications/';
   const title=document.createElement('strong'),message=document.createElement('span');title.textContent=item.title;message.textContent=item.message;link.append(title,message);
   link.addEventListener('click',event=>{event.preventDefault();post(item.read_url,{}).finally(()=>location.assign(link.href));});
   const close=document.createElement('button');close.type='button';close.textContent='×';close.setAttribute('aria-label',en?'Close':'Закрыть');close.addEventListener('click',()=>toast.remove());
   toast.append(link,close);tray.prepend(toast);while(tray.children.length>5)tray.lastChild.remove();
   if(item.severity!=='critical')setTimeout(()=>toast.remove(),8000);
  }
  async function poll(){if(polling||document.hidden)return;polling=true;try{
   const res=await fetch(body.dataset.notificationFeed+'?after='+encodeURIComponent(cursor)+'&after_id='+encodeURIComponent(cursorId),{headers:{'X-Requested-With':'XMLHttpRequest'}});
   if(!res.ok||res.redirected)return;const data=await res.json();cursor=data.cursor;cursorId=data.cursor_id||"";toasts=data.toasts;badge(data.unread);data.items.forEach(show);
  }catch{}finally{polling=false;}}
  document.querySelectorAll('[data-notification-read]').forEach(link=>link.addEventListener('click',event=>{event.preventDefault();post(link.dataset.notificationRead,{}).finally(()=>location.assign(link.href));}));
  navigator.serviceWorker?.addEventListener('message',event=>{
   if(event.data?.type==='crm-notification'&&event.data.data.user_id===user){show(event.data.data);poll();}
  });
  window.addEventListener('storage',event=>{if(event.key===seenKey){try{seen=new Set(JSON.parse(event.newValue||'[]'));}catch{}}});
  setInterval(poll,15000);poll();document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
  const url=new URL(location.href),notification=url.searchParams.get('notification');
  if(notification&&/^[0-9a-f-]{36}$/.test(notification))post('/notifications/'+notification+'/read/',{}).then(poll);
  const button=document.getElementById('enable-browser-push'),status=document.getElementById('browser-push-status');
  const onboarding=document.getElementById('push-onboarding');
  const enable=onboarding?.querySelector('[data-push-enable]');
  const dismiss=onboarding?.querySelector('[data-push-dismiss]');
  const promptStatus=onboarding?.querySelector('[data-push-status]');
  const promptKey='crm-push-prompt-v1:'+user;
  const standalone=window.matchMedia('(display-mode: standalone)').matches||navigator.standalone===true;
  const ios=/iPhone|iPad|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
  const needsInstall=ios&&!standalone;
  const stageKey=promptKey+(needsInstall?':install':':permission');
  const supported='Notification' in window&&'serviceWorker' in navigator&&'PushManager' in window&&window.isSecureContext;
  const remember=()=>{try{localStorage.setItem(stageKey,'seen');}catch{}};
  const wasShown=()=>{try{return localStorage.getItem(stageKey)==='seen';}catch{return false;}};
  async function subscribe(publicKey,enablePreference=false) {
    const registration=await navigator.serviceWorker.register('/crm-sw.js');
    await navigator.serviceWorker.ready;
    const raw=publicKey.replace(/-/g,'+').replace(/_/g,'/');
    const bytes=Uint8Array.from(atob(raw+'='.repeat((4-raw.length%4)%4)),ch=>ch.charCodeAt(0));
    const subscription=await registration.pushManager.getSubscription()||await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:bytes});
    const res=await post('/notifications/push/subscribe/',{...subscription.toJSON(),enable_push:enablePreference});
    if(!res.ok)throw new Error('server');
    body.dataset.notificationPush='1';
  }
  async function enablePush(target,output,publicKey) {
    if(needsInstall||!supported){output.textContent=needsInstall?'Добавьте CRM на экран «Домой» и откройте её оттуда.':'Этот браузер не поддерживает системные push-уведомления.';return;}
    target.disabled=true;
    try {
      // Request before the first await to preserve the click's user activation.
      const permission=await Notification.requestPermission();
      if(permission!=='granted'){
        remember();output.textContent=permission==='denied'?'Уведомления запрещены в браузере. Разрешение можно изменить в настройках сайта.':'Разрешение не получено. Включить уведомления можно позже в настройках CRM.';return;
      }
      await subscribe(publicKey,true);remember();
      output.textContent=en?'Push enabled on this device.':'Уведомления включены на этом устройстве.';
      if(onboarding?.open)onboarding.close();
    }catch{output.textContent=en?'Could not enable push. Try again later.':'Не удалось включить уведомления. Попробуйте ещё раз.';}
    finally{target.disabled=false;}
  }
  button?.addEventListener('click',()=>enablePush(button,status,button.dataset.publicKey));
  enable?.addEventListener('click',()=>enablePush(enable,promptStatus,body.dataset.pushPublicKey));
  dismiss?.addEventListener('click',()=>{remember();onboarding.close();});
  onboarding?.addEventListener('cancel',remember);
  if(onboarding&&!wasShown()&&(needsInstall||(supported&&Notification.permission!=='denied'&&(Notification.permission==='default'||body.dataset.notificationPush!=='1')))){
    if(needsInstall){onboarding.querySelector('[data-push-install]').hidden=false;enable.hidden=true;dismiss.textContent='Понятно';}
    onboarding.showModal();
  }else if(supported&&Notification.permission==='granted'&&body.dataset.notificationPush==='1'){
    // Restore this device's subscription after logout without prompting again.
    subscribe(body.dataset.pushPublicKey).catch(()=>{});
  }
  document.querySelectorAll('form[action="/logout/"]').forEach(form=>form.addEventListener('submit',async event=>{
   if(event.defaultPrevented)return;
   if(form.dataset.pushCleared||!navigator.serviceWorker)return;event.preventDefault();form.dataset.pushCleared='1';
   try{const registration=await navigator.serviceWorker.getRegistration('/'),subscription=await registration?.pushManager.getSubscription();if(subscription){await post('/notifications/push/unsubscribe/',{endpoint:subscription.endpoint});await subscription.unsubscribe();}}catch{}
   form.submit();
  }));
 });
})();
