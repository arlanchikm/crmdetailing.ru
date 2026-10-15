(()=>{
  const dialog=document.getElementById('booking-map-dialog');
  if(!dialog||typeof dialog.showModal!=='function')return;
  const frame=dialog.querySelector('iframe'),routeButton=dialog.querySelector('.booking-route-button');
  let cancelRoute=()=>{};
  document.querySelectorAll('[data-booking-map-open]').forEach(link=>link.addEventListener('click',event=>{
    event.preventDefault();
    if(!frame.hasAttribute('src'))frame.src=frame.dataset.src;
    dialog.showModal();
  }));
  dialog.querySelector('[data-booking-map-close]').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>cancelRoute());
  dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close()}});
  routeButton.addEventListener('click',event=>{
    const ua=navigator.userAgent,apple=/iPhone|iPad|iPod/.test(ua)||(/Macintosh/.test(ua)&&navigator.maxTouchPoints>1);
    cancelRoute();
    if(/Android/.test(ua)){
      event.preventDefault();window.location.href=routeButton.dataset.android;return;
    }
    // Desktop browsers use Yandex Maps on the web. On iOS, try installed apps.
    if(!apple)return;
    event.preventDefault();
    let timer,stopped=false;
    const cleanup=()=>{
      stopped=true;clearTimeout(timer);
      document.removeEventListener('visibilitychange',onVisibility);
      window.removeEventListener('pagehide',cleanup);
      window.removeEventListener('blur',cleanup);
    };
    const onVisibility=()=>{if(document.hidden)cleanup()};
    cancelRoute=cleanup;
    document.addEventListener('visibilitychange',onVisibility);
    window.addEventListener('pagehide',cleanup);
    // Do not open another app over an OS confirmation or a successful launch.
    window.addEventListener('blur',cleanup);
    const attempt=(url,next)=>{
      if(stopped||document.hidden)return;
      timer=setTimeout(()=>{if(!stopped&&!document.hidden)next()},1800);
      try{window.location.href=url}catch(error){clearTimeout(timer);next()}
    };
    attempt(routeButton.dataset.yandexApp,()=>attempt(routeButton.dataset.appleApp,()=>{
      cleanup();window.location.href=routeButton.dataset.google;
    }));
  });
})();
