(()=>{
  if(matchMedia('(max-width:700px)').matches) document.querySelectorAll('.kb-sidebar details').forEach(item=>item.open=false);
  const search=document.querySelector('.kb-search input');
  document.addEventListener('keydown',event=>{
    if(event.key==='/'&&search&&!['INPUT','TEXTAREA'].includes(document.activeElement?.tagName)&&!document.activeElement?.isContentEditable){event.preventDefault();search.focus()}
  });
})();
