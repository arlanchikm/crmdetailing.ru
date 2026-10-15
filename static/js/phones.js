(()=>{
 const error='Укажите российский номер в формате +7 (800) 555-35-35.';
 function bind(input){
  if(input.dataset.phoneReady)return;input.dataset.phoneReady='1';
  input.type='tel';input.autocomplete='tel';input.inputMode='tel';input.placeholder='+7 (800) 555-35-35';
  input.pattern='\\+7 \\([0-9]{3}\\) [0-9]{3}-[0-9]{2}-[0-9]{2}';
  const update=()=>{
   const raw=input.value,caret=input.selectionStart??raw.length;
   const before=(raw.slice(0,caret).match(/\d/g)||[]).length;
   let digits=raw.replace(/\D/g,'');
   if(!digits){if(raw.trim())input.setCustomValidity(error);else input.setCustomValidity('');return;}
   if(/[^\d+()\s-]/.test(raw)||digits.length>11||(digits.length===11&&!/^[78]/.test(digits))){input.setCustomValidity(error);return;}
   let added=0;
   if(digits.length===10&&!raw.trim().startsWith('+')){digits='7'+digits;added=1;}
   else if(digits[0]==='8')digits='7'+digits.slice(1);
   else if(digits[0]!=='7'){digits='7'+digits;added=1;}
   const n=digits.slice(1);
   let text='+7';if(n.length)text+=' ('+n.slice(0,3);if(n.length>=3)text+=')';
   if(n.length>3)text+=' '+n.slice(3,6);if(n.length>6)text+='-'+n.slice(6,8);if(n.length>8)text+='-'+n.slice(8,10);
   input.value=text;
   let position=text.length,count=0,target=before+added;
   for(let i=0;i<text.length;i++)if(/\d/.test(text[i])&&++count===target){position=i+1;break;}
   if(caret<raw.length)input.setSelectionRange(position,position);
   input.setCustomValidity(digits.length===11?'':error);
  };
  input.addEventListener('input',update);input.addEventListener('change',update);
  input.addEventListener('keydown',event=>{
   if(!['Backspace','Delete'].includes(event.key)||input.selectionStart!==input.selectionEnd)return;
   const pos=input.selectionStart,forward=event.key==='Delete';let i=forward?pos:pos-1;
   if(i<0||i>=input.value.length||/\d/.test(input.value[i]))return;
   while(i>=0&&i<input.value.length&&!/\d/.test(input.value[i]))i+=forward?1:-1;
   if(i>=0&&i<input.value.length){event.preventDefault();input.value=input.value.slice(0,i)+input.value.slice(i+1);input.setSelectionRange(i,i);update();}
  });
  if(input.value)update();
 }
 window.initPhoneInputs=(root=document)=>{
  if(root.matches?.('[data-phone-input],input[name="phone"],input[name="client_phone"]'))bind(root);
  root.querySelectorAll?.('[data-phone-input],input[name="phone"],input[name="client_phone"]').forEach(bind);
 };
 document.addEventListener('DOMContentLoaded',()=>{
  window.initPhoneInputs();new MutationObserver(changes=>changes.forEach(change=>change.addedNodes.forEach(node=>{if(node.nodeType===1)window.initPhoneInputs(node);}))).observe(document.body,{childList:true,subtree:true});
 });
})();
