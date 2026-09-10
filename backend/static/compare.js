/* Read-only range comparison. Fetches two days concurrently, never invokes LLM reports. */
(() => {
  const UI=ActivityUI;
  let generation=0,loaded=[];
  const start=document.getElementById('compareStart'),end=document.getElementById('compareEnd');
  const dimension=document.getElementById('compareDimension');
  const state=document.getElementById('compareState');
  const seconds=(day,key)=>day.category.categories.find(x=>x.category===key)?.seconds||0;
  const go=day=>{location.href=`/?day=${encodeURIComponent(day)}`;};
  function render() {
    const dim=dimension.value;
    const names=[...new Set(loaded.flatMap(day=>day[dim].categories.filter(x=>x.seconds>0).map(x=>x.category)))];
    const dates=loaded.map(x=>x.day);
    const series=names.map(name=>({id:name,name,type:'bar',stack:'time',barMaxWidth:44,itemStyle:{color:UI.colors[name]||UI.colors.其他},emphasis:{focus:'series'},data:loaded.map(day=>(day[dim].categories.find(x=>x.category===name)?.seconds||0)/3600)}));
    const bars=UI.chart('compareBars',{aria:{enabled:true},grid:{left:46,right:20,top:25,bottom:50},tooltip:{trigger:'axis',axisPointer:{type:'shadow'},formatter:items=>`${UI.escape(items[0]?.axisValue||'')}<br>`+items.filter(x=>x.value>0).map(x=>`${UI.escape(x.seriesName)}：${UI.duration(x.value*3600)}`).join('<br>')},xAxis:{type:'category',data:dates,axisLabel:{color:'#afc1cf',formatter:x=>x.slice(5)}},yAxis:{type:'value',name:'小时',axisLabel:{color:'#afc1cf'},splitLine:{lineStyle:{color:'#30404f'}}},series});
    if(bars){bars.off('click');bars.on('click',p=>go(dates[p.dataIndex]));}
    document.getElementById('compareLegend').innerHTML=names.map(name=>`<span><i style="background:${UI.colors[name]||UI.colors.其他}"></i>${UI.escape(name)}</span>`).join('');
    const hours=(iso,day)=>iso.slice(0,10)>day?24:Number(iso.slice(11,13))+Number(iso.slice(14,16))/60+Number(iso.slice(17,19)||0)/3600;
    const parts=loaded.flatMap((day,row)=>day.timeline.segments.map(s=>({row,start:hours(s.start_time_local,day.day),end:hours(s.end_time_local,day.day),name:dim==='purpose'?(s.purpose||s.category):s.category,segment:s,day:day.day}))).filter(x=>x.end>x.start);
    document.getElementById('compareLanes').style.height=`${Math.max(220,loaded.length*38+65)}px`;
    const lanes=UI.chart('compareLanes',{aria:{enabled:true},grid:{left:75,right:24,top:12,bottom:35},tooltip:{formatter:p=>{const x=p.data.value[3];return `${x.day}<br>${UI.clock(x.segment.start_time_local)}—${UI.clock(x.segment.end_time_local)} · ${UI.escape(x.name)}<br>${UI.escape(x.segment.behavior||'')} · ${UI.duration(x.segment.duration_seconds)}`;}},xAxis:{type:'value',min:0,max:24,interval:4,axisLabel:{color:'#afc1cf',formatter:x=>`${String(x).padStart(2,'0')}:00`},splitLine:{lineStyle:{color:'#30404f'}}},yAxis:{type:'category',inverse:true,data:dates,axisLabel:{color:'#afc1cf',formatter:x=>x.slice(5)},axisTick:{show:false}},series:[{id:'lanes',type:'custom',data:parts.map(x=>({value:[x.row,x.start,x.end,x],itemStyle:{color:UI.colors[x.name]||UI.colors.其他}})),renderItem:(params,api)=>{const from=api.coord([api.value(1),api.value(0)]),to=api.coord([api.value(2),api.value(0)]);return {type:'rect',shape:{x:from[0],y:from[1]-9,width:Math.max(1,to[0]-from[0]),height:18,r:2},style:api.style()};}}]});
    if(lanes){lanes.off('click');lanes.on('click',p=>go(p.data.value[3].day));}
    const life=['睡眠','运动','出游','用餐','通勤','休息','家务'];
    document.getElementById('compareTable').innerHTML=loaded.map(day=>`<tr><td><a href="/?day=${day.day}">${day.day}${day.day===UI.today()?'（进行中）':''}</a></td>${['学习','工作','娱乐'].map(c=>`<td>${UI.duration(seconds(day,c))}</td>`).join('')}<td>${UI.duration(life.reduce((s,k)=>s+seconds(day,k),0))}</td><td>${UI.duration(seconds(day,'空闲')+seconds(day,'其他'))}</td><td>${UI.duration(seconds(day,'无设备记录'))}</td></tr>`).join('');
  }
  async function load() {
    const current=++generation;
    const days=[];
    const error=message=>{state.textContent=message;state.classList.add('is-error');document.getElementById('compareResults').hidden=true;};
    if(!UI.validDay(start.value)||!UI.validDay(end.value)||end.value<start.value||end.value>UI.today()){error('请选择有效的起止日期，结束日期不能晚于今天。');return;}
    for(let d=start.value;d<=end.value;d=UI.shift(d,1)){days.push(d);if(days.length>14){error('一次最多比较 14 天，请缩短日期范围。');return;}}
    state.classList.remove('is-error');state.textContent='正在读取历史活动…';document.getElementById('compareResults').hidden=true;
    const results=new Array(days.length);let cursor=0;
    const worker=async()=>{while(cursor<days.length){if(current!==generation)return;const index=cursor++,day=days[index];const [category,purpose,timeline]=await Promise.all([UI.json(`/api/v1/summary/today?day=${day}`),UI.json(`/api/v1/summary/today?day=${day}&dimension=purpose`),UI.json(`/api/v1/timeline/combined?day=${day}`)]);results[index]={day,category,purpose,timeline};}};
    try{await Promise.all([worker(),worker()]);if(current!==generation)return;loaded=results;UI.setTimezone(loaded[0]?.timeline.timezone);document.getElementById('compareResults').hidden=false;render();UI.syncDay(end.value);state.textContent=`已加载 ${start.value} 至 ${end.value}，共 ${days.length} 天；点击图表或日期回看当天。`;}catch(e){if(current!==generation)return;error(`${e.message}；请点击“更新对比”重试，失败日期不会被当作零数据。`);}
  }
  document.getElementById('compareForm').onsubmit=e=>{e.preventDefault();load();};
  dimension.onchange=()=>{if(loaded.length)render();};
  document.getElementById('recentWeek').onclick=()=>{end.value=UI.today();start.value=UI.shift(end.value,-6);load();};
  UI.ready.then(()=>{const query=new URLSearchParams(location.search);end.max=start.max=UI.today();end.value=UI.validDay(query.get('end'))&&query.get('end')<=UI.today()?query.get('end'):UI.initialDay();start.value=UI.validDay(query.get('start'))?query.get('start'):UI.shift(end.value,-6);const url=new URL(location.href);url.searchParams.delete('start');url.searchParams.delete('end');history.replaceState(null,'',url);load();});
})();
