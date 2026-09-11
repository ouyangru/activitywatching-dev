/* Shared dates, chart lifecycle and interval correction for all activity views. */
window.ActivityUI = (() => {
  if (window.parent !== window) window.__PERSONAL_HUB_EMBEDDED__ = true;
  const colors = { 学习:'#82cedd', 工作:'#92a3d9', 娱乐:'#d6ad79', 空闲:'#8d9ba9', 其他:'#b3a0ce', 无设备记录:'#45515e', 睡眠:'#858ac4', 运动:'#78b6a1', 出游:'#c6bc80', 用餐:'#c4937c', 通勤:'#779fb7', 休息:'#aa9eb6', 家务:'#99ad7b', 生活事务:'#99ad7b' };
  const editable = Object.keys(colors).filter(x => !['无设备记录','生活事务'].includes(x));
  const charts = new Map();
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
  const today = () => new Intl.DateTimeFormat('en-CA', {timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).filter(x=>['year','month','day'].includes(x.type)).reduce((a,x)=>(a[x.type]=x.value,a),{});
  const dayKey = () => { const p=today(); return `${p.year}-${p.month}-${p.day}`; };
  const validDay = value => /^\d{4}-\d{2}-\d{2}$/.test(value || '') && !Number.isNaN(Date.parse(value+'T12:00:00Z')) && new Date(value+'T12:00:00Z').toISOString().slice(0,10)===value;
  const shift = (day,n) => { const d=new Date(day+'T12:00:00Z'); d.setUTCDate(d.getUTCDate()+n); return d.toISOString().slice(0,10); };
  const duration = value => { const s=Math.max(0,Number(value)||0); if(s<60)return `${Math.round(s)}秒`; const m=Math.round(s/60); return m>=60 ? `${Math.floor(m/60)}h${m%60 ? ` ${m%60}min` : ''}` : `${m}min`; };
  const clock = value => new Intl.DateTimeFormat('zh-CN',{timeZone:timezone,hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
  const platformLabel = value => value === 'android' ? 'Android' : value === 'windows' ? 'Windows' : '无设备';
  const errorLike = value => /(failed|failure|invalid|error|异常|失败|不可用|无法)/i.test(String(value || ''));
  const safePath = value => {
    const raw=String(value || '').trim();
    if(!raw)return '';
    try { return new URL(raw, document.baseURI).pathname; }
    catch { return raw.split('?',1)[0].slice(0,500); }
  };
  function reportError(error, action='client_error', details={}) {
    const message = error instanceof Error ? error.message : String(error || 'client error');
    const stack = error instanceof Error ? error.stack || '' : String(details.stack || '');
    fetch('/api/v1/debug/client-error', {
      method:'POST', credentials:'same-origin', keepalive:true,
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        action,
        message:message.slice(0,1200),
        stack:stack.slice(0,5000),
        page:location.pathname,
        source:safePath(details.source || ''),
        line:details.line || null,
        column:details.column || null,
      }),
    }).catch(()=>{});
  }
  function toast(message) {
    const el=document.getElementById('toast');
    if(el){el.textContent=message;el.classList.add('visible');setTimeout(()=>el.classList.remove('visible'),2200);}
    if(errorLike(message)) reportError(message,'toast_error');
  }
  function rankingRows(items,emptyText) {
    return items.length ? items.map(x=>`<li class="ranking-row">${x.title!=null?`<span class="ranking-name" title="${escape(x.title)}">`:'<span class="ranking-name">'}${escape(x.name)}</span><span class="ranking-bar"><i style="width:${x.width}%"></i></span><b class="ranking-value">${escape(x.value)}</b></li>`).join('') : `<li class="ranking-empty">${escape(emptyText)}</li>`;
  }
  async function json(url, options) {
    const response=await fetch(url,options);
    if(!response.ok) throw new Error(response.status===401 ? '登录已过期，请重新登录' : `请求失败（${response.status}），请重试`);
    return response.json();
  }
  function setTimezone(value) { if(value)timezone=value; }
  const ready = json('/api/v1/timeline/today').then(data=>{timezone=data.timezone || timezone;return data;}).catch(error=>{reportError(error,'initial_timeline_load');return null;});
  function initialDay() {
    const d=new URLSearchParams(location.search).get('day');
    // Consume the navigation-only day param so a refresh always lands on today.
    if(d!==null){const url=new URL(location.href);url.searchParams.delete('day');history.replaceState(null,'',url);}
    return validDay(d)&&d<=dayKey()?d:dayKey();
  }
  function syncDay(day) {
    document.querySelectorAll('[data-day-link]').forEach(a=>{const u=new URL(a.href);u.searchParams.set('day',day);a.href=u;});
    const note=document.getElementById('timezoneNote');if(note)note.textContent=`日期与图表使用 ${timezone} 时区`;
  }
  function bindDate({input,prev,next,today:home,onChange,day}) {
    const field=document.getElementById(input);let selected=day;
    const render=()=>{field.value=selected;field.max=dayKey();document.getElementById(next).disabled=selected>=dayKey();syncDay(selected);};
    const change=d=>{if(!validDay(d)||d>dayKey()){render();return;}selected=d;render();onChange(d);};
    document.getElementById(prev).onclick=()=>change(shift(selected,-1));document.getElementById(next).onclick=()=>change(shift(selected,1));document.getElementById(home).onclick=()=>change(dayKey());field.onchange=()=>change(field.value);render();
    return {set:change};
  }
  function chart(id,option) {
    const el=document.getElementById(id); if(!el)return null;
    if(!window.echarts){el.innerHTML='<p class="empty">图表暂不可用，下方仍可查看文字数据。</p>';return null;}
    let instance=charts.get(id);if(instance&&instance.getDom()!==el){instance.dispose();instance=null;}
    if(!instance){el.textContent='';instance=echarts.init(el);charts.set(id,instance);}
    instance.setOption({animation:!reduced.matches,animationDuration:450,animationDurationUpdate:450,animationEasingUpdate:'cubicOut',backgroundColor:'transparent',textStyle:{color:'#bac8d5',fontFamily:'Segoe UI, Microsoft YaHei, sans-serif'},...option},{replaceMerge:['series']});return instance;
  }
  function disposeWithin(el){charts.forEach((c,id)=>{if(el.contains(c.getDom())){c.dispose();charts.delete(id);}});}
  function pie(id,items,onSelect) {
    const data=items.filter(x=>x.seconds>0);const total=data.reduce((a,x)=>a+x.seconds,0);
    const c=chart(id,{aria:{enabled:true},tooltip:{trigger:'item',formatter:p=>`${escape(p.name)}<br>${duration(p.value)} · ${p.percent}%`},series:[{id:'distribution',type:'pie',radius:['54%','76%'],center:['50%','48%'],minAngle:1,itemStyle:{borderColor:'#1b242e',borderWidth:2,borderRadius:3},label:{show:false},emphasis:{scaleSize:5},data:data.map(x=>({name:x.category,value:x.seconds,itemStyle:{color:colors[x.category]||colors.其他}}))}],graphic:[{id:'total',type:'text',left:'center',top:'43%',style:{text:total?duration(total):'暂无记录',fill:'#e5edf4',font:'500 19px Segoe UI'}},{id:'hint',type:'text',left:'center',top:'56%',style:{text:'分类时长',fill:'#9aabba',font:'12px Segoe UI'}}]});
    if(c){c.off('click');if(onSelect)c.on('click',p=>onSelect(p.name));}return c;
  }
  function flash(el){if(!el)return;el.classList.remove('is-revealed');void el.offsetWidth;el.classList.add('is-revealed');}
  window.addEventListener('resize',()=>charts.forEach(c=>c.resize()));
  window.addEventListener('error',event=>reportError(event.error || event.message,'window_error',{source:event.filename,line:event.lineno,column:event.colno,stack:event.error?.stack || ''}));
  window.addEventListener('unhandledrejection',event=>reportError(event.reason || 'Unhandled promise rejection','unhandled_rejection',{stack:event.reason?.stack || ''}));
  reduced.addEventListener('change',()=>charts.forEach(c=>c.setOption({animation:!reduced.matches})));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)charts.forEach(c=>c.resize());});
  let intervalDialog;
  function correctInterval(segment,onSaved) {
    if(!intervalDialog){intervalDialog=document.createElement('dialog');intervalDialog.className='correction-dialog';intervalDialog.innerHTML=`<form><h3>修正这段时间</h3><p class="correction-summary">可调整起止时间，只覆盖空闲或无设备记录，原始采集记录保留。</p><label>开始<input name="start" type="datetime-local" step="1" required></label><label>结束<input name="end" type="datetime-local" step="1" required></label><label>实际活动<select name="category">${editable.map(x=>`<option>${x}</option>`).join('')}</select></label><label>备注<input name="note" maxlength="280" placeholder="例如：阅读纸质教材"></label><label class="correction-remember"><input name="remember" type="checkbox"><span>让系统参考这个时段习惯</span></label><p class="correction-summary" data-local-zone></p><p role="alert" class="form-error"></p><div class="correction-actions"><button type="button" data-cancel class="ghost-button">取消</button><button class="ghost-button correction-save" type="submit">保存修正</button></div></form>`;document.body.appendChild(intervalDialog);intervalDialog.querySelector('[data-cancel]').onclick=()=>intervalDialog.close();}
    const form=intervalDialog.querySelector('form');form.reset();form.querySelector('.form-error').textContent='';
    const local=value=>{const d=new Date(value);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}T${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;};
    form.elements.start.value=local(segment.start_time);form.elements.end.value=local(segment.end_time);form.elements.category.value=editable.includes(segment.category)?segment.category:'学习';
    form.querySelector('[data-local-zone]').textContent=`填写时间使用本机时区 ${Intl.DateTimeFormat().resolvedOptions().timeZone}，保存后自动换算。`;
    form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('[type=submit]');button.disabled=true;try{const start=new Date(form.elements.start.value),end=new Date(form.elements.end.value);if(!(end>start)||end>new Date()||end-start>48*3600000)throw new Error('结束必须晚于开始，不能在未来，最长 48 小时');await json('/api/v1/offline-activities',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start_time:start.toISOString(),end_time:end.toISOString(),category:form.elements.category.value,note:form.elements.note.value,remember:form.elements.remember.checked})});intervalDialog.close();await onSaved();}catch(error){reportError(error,'correct_interval');form.querySelector('.form-error').textContent=error.message;}finally{button.disabled=false;}};
    intervalDialog.showModal();
  }
  function reflection(container,segments,insights,onSelect) {
    const target=document.getElementById(container);if(!target)return;
    const longest=segments.filter(x=>['学习','工作'].includes(x.category)).sort((a,b)=>b.duration_seconds-a.duration_seconds)[0];
    const video=segments.filter(x=>x.category==='娱乐').sort((a,b)=>b.duration_seconds-a.duration_seconds)[0];
    const gap=segments.filter(x=>['空闲','无设备记录','其他'].includes(x.category)).sort((a,b)=>b.duration_seconds-a.duration_seconds)[0];
    const rows=[];
    if(longest)rows.push({segment:longest,title:`可以保留 · ${duration(longest.duration_seconds)} 的连续${longest.category}`,text:'回看这段时间是否推进了重要任务；活动记录表示时间投入，不代表已经完成任务。'});
    if(video)rows.push({segment:video,title:`值得回看 · ${duration(video.duration_seconds)} 的娱乐`,text:'这段休息是否符合原本的安排？如果比预期更久，可以在下次开始前设定结束时间。'});
    if(gap)rows.push({segment:gap,title:`待确认 · ${duration(gap.duration_seconds)} 的${gap.category}`,text:'补充实际活动后，时间分布会更准确；这段时间不会直接被当作浪费。'});
    target.innerHTML=rows.length?rows.map((x,i)=>`<article class="reflection-item"><span class="reflection-number">0${i+1}</span><div><h3>${escape(x.title)}</h3><p>${escape(x.text)}</p><button class="ghost-button" data-evidence="${i}">查看 ${clock(x.segment.start_time_local)}—${clock(x.segment.end_time_local)} 的记录</button></div></article>`).join(''):'<p class="empty">尚无足够活动记录，暂不生成复盘建议。</p>';
    target.querySelectorAll('[data-evidence]').forEach(b=>b.onclick=()=>onSelect(rows[Number(b.dataset.evidence)].segment));
  }
  return {colors,editable,ready,setTimezone,initialDay,today:dayKey,validDay,shift,duration,clock,platformLabel,toast,rankingRows,escape,json,reportError,chart,pie,disposeWithin,bindDate,syncDay,flash,correctInterval,reflection,get timezone(){return timezone;}};
})();
