(() => {
  'use strict';

  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) {
    try { tg.ready(); tg.expand(); tg.setHeaderColor?.('#070a14'); tg.setBackgroundColor?.('#070a14'); tg.enableClosingConfirmation?.(); } catch (_) {}
  }

  const $ = id => document.getElementById(id);
  const screenEl = $('screen');
  const initData = tg?.initData || '';
  let state = null;

  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  const beastArt = {
    'Frost Wolf':'frostpaw','Ember Drake':'blazehorn','Flame Raptor':'blazehorn','Inferno Dragon':'solaris','Aether Dragon':'stormwing','Eternal Phoenix':'solaris','Frost Wyvern':'frostfang','Ice Panther':'frostfang','Thunder Lynx':'stormwing','Storm Hawk':'stormwing','Thunder Beast':'stormwing','Mystic Serpent':'aquaserpent','Shadow Viper':'nightshade','Void Serpent':'voidwyrm','Shadow Dragon':'voidwyrm','Stone Golem':'stoneguardian','Ancient Phoenix':'solaris','Celestial Wolf':'skydancer'
  };
  const art = name => `/static/assets/beasts/${beastArt[name] || 'frostpaw'}.jpg`;
  const rarityClass = r => String(r || 'Common').replace(/\s/g, '');

  function showStatus(message, error=false) {
    if (!screenEl) return;
    screenEl.innerHTML = `<div class="empty" style="padding:40px 20px"><div style="font-size:38px;margin-bottom:12px">${error ? '⚠️' : '⏳'}</div><b>${esc(message)}</b>${error ? '<br><br><button class="secondary" onclick="location.reload()">RETRY</button>' : ''}</div>`;
  }

  async function api(path, opts = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const headers = {'Content-Type':'application/json'};
      if (initData) headers['X-Telegram-Init-Data'] = initData;
      const r = await fetch('/api' + path, {...opts, headers:{...headers,...(opts.headers||{})}, signal:controller.signal, cache:'no-store'});
      let d = {};
      try { d = await r.json(); } catch (_) {}
      if (!r.ok) throw Error(d.detail || `Server error ${r.status}`);
      return d;
    } catch (e) {
      if (e.name === 'AbortError') throw Error('Server is not responding. Check Render deployment.');
      throw e;
    } finally { clearTimeout(timer); }
  }

  function toast(x) { const el=$('toast'); if(!el)return; el.textContent=x; el.classList.add('show'); setTimeout(()=>el.classList.remove('show'),2200); }
  function renderStats(){ if(!state)return; $('level').textContent=state.player.level; $('aether').textContent=state.player.aether; $('xp').textContent=state.player.level>=50?'MAX':`${state.player.xp}/${state.xp_needed}`; $('welcome').textContent=state.player.username?`@${state.player.username}`:'Beast Master'; }
  function setActive(s){ document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.screen===s)); }

  function petCard(x){return `<div class="card pet" data-pet="${x.id}"><img src="${art(x.name)}" alt="${esc(x.name)}"><b>${esc(x.name)}</b><div class="rarity ${rarityClass(x.rarity)}">${esc(x.rarity)}</div><div>⚡ ${x.power}</div><small>Lv.${x.level} · ${esc(x.element)}</small></div>`}
  function home(){const p=state.player,pet=state.top_pet,pct=p.level>=50?100:Math.min(100,p.xp/state.xp_needed*100);screenEl.innerHTML=`<div class="hero"><img class="beast-large" src="${art(pet?.name||'Frost Wolf')}" onerror="this.style.visibility='hidden'"><h1>${esc(pet?.name||'Frost Wolf')}</h1><div class="muted">${esc(pet?.rarity||'Common')} · ⚡ ${pet?.power||0}</div><div class="progress"><i style="width:${pct}%"></i></div><p class="muted">Level ${p.level} · ${p.level>=50?'MAX':`${p.xp}/${state.xp_needed} XP`}</p><button class="primary" data-go="summon">✦ SUMMON A BEAST</button></div><div class="grid"><div class="card"><b>🎁 Daily Reward</b><p class="muted">Streak: ${state.daily?.streak||0}</p><button class="secondary" data-action="daily">CLAIM</button></div><div class="card"><b>📜 Quests</b><p class="muted">Earn AETHER & XP.</p><button class="secondary" data-go="quests">OPEN</button></div></div><div class="section-title"><b>Quick Actions</b></div><div class="grid"><button class="secondary" data-go="pets">🐲 Collection</button><button class="secondary" data-go="battle">⚔ Arena</button><button class="secondary" data-go="merge">🧬 Merge</button><button class="secondary" data-go="leaderboard">🏆 Ranking</button></div>`}
  function pets(){const ps=state.pets;screenEl.innerHTML=`<div class="section-title"><b>🐲 Beast Collection</b><span class="pill">${ps.length}</span></div><div class="grid">${ps.map(petCard).join('')||'<div class="empty">Your collection is empty.</div>'}</div>`}
  function petDetails(id){const x=state.pets.find(p=>Number(p.id)===Number(id));if(!x)return;screenEl.innerHTML=`<div class="hero"><img class="beast-large" src="${art(x.name)}"><div class="rarity ${rarityClass(x.rarity)}">${esc(x.rarity)}</div><h1>${esc(x.name)}</h1><p class="muted">${esc(x.element)} element · Level ${x.level}</p><div class="grid"><div class="card"><small>POWER</small><div class="big-number">⚡${x.power}</div></div><div class="card"><small>XP</small><div class="big-number">${x.xp||0}</div></div></div><button class="primary" data-go="pets">BACK TO COLLECTION</button></div>`}
  function summon(){screenEl.innerHTML=`<div class="hero"><div class="big-number">✦</div><h1>Summon Chamber</h1><p class="muted">Open an Aether portal and discover your next Beast.</p><div class="grid"><div class="card"><b>1× SUMMON</b><div class="big-number">25</div><small>AETHER</small><button class="primary" data-summon="1">SUMMON</button></div><div class="card"><b>10× SUMMON</b><div class="big-number">225</div><small>SAVE 25 AETHER</small><button class="primary" data-summon="10">SUMMON ×10</button></div></div></div><div class="card"><b>Drop Rates</b><p class="muted">⚪ Common 60% · 🟢 Uncommon 25% · 🔵 Rare 10% · 🟣 Epic 4% · 🟡 Legendary 1%</p></div>`}
  async function doSummon(count){try{const d=await api('/summon',{method:'POST',body:JSON.stringify({count})});state=await api('/state');renderStats();summon();showResults(d.results)}catch(e){toast(e.message)}}
  function showResults(rs){const html=rs.map(x=>`<div class="result-card"><img src="${art(x.name)}"><b>${esc(x.name)}</b><div class="rarity ${rarityClass(x.rarity)}">${esc(x.rarity)}</div><small>⚡${x.power}</small></div>`).join('');const m=document.createElement('div');m.className='modal';m.innerHTML=`<div class="sheet"><div class="close" data-close>✕</div><h2>✦ Summon Results</h2><div class="result-grid">${html}</div><br><button class="primary" data-close>CONTINUE</button></div>`;document.body.appendChild(m)}
  async function daily(){try{const d=await api('/daily',{method:'POST'});toast(`🎁 +${d.reward} AETHER · Streak ${d.streak}`);state=await api('/state');renderStats();home()}catch(e){toast(e.message)}}
  async function quests(){try{const d=await api('/quests');screenEl.innerHTML=`<div class="section-title"><b>📜 Daily Quests</b></div><div class="list">${d.quests.map(q=>`<div class="item"><div><b>${esc(q.title)}</b><small>${q.progress}/${q.target} · +${q.reward} AETHER</small></div>${q.claimed?'✅':q.done?`<button class="secondary" data-quest="${q.code}">CLAIM</button>`:'⏳'}</div>`).join('')}</div>`}catch(e){toast(e.message)}}
  async function battle(){screenEl.innerHTML=`<div class="arena"><h1>⚔ Aether Arena</h1><p class="muted">Your strongest three Beasts enter combat.</p><div class="vs"><div class="fighter"><img src="${art(state.top_pet?.name||'Frost Wolf')}"><b>${esc(state.top_pet?.name||'Your Beast')}</b><div class="hp"><i style="width:100%"></i></div></div><div class="big-number">VS</div><div class="fighter"><img src="${art('Shadow Dragon')}"><b>Void Warden</b><div class="hp"><i style="width:100%"></i></div></div></div><br><button class="primary" data-fight>FIGHT</button></div>`}
  async function fight(){try{const d=await api('/battle',{method:'POST'});state=await api('/state');renderStats();screenEl.innerHTML=`<div class="hero"><div class="big-number">${d.won?'🏆':'💀'}</div><h1>${d.won?'Victory!':'Defeat'}</h1><p>⚔ Your power: <b>${d.player_power}</b></p><p>👹 Enemy: <b>${d.enemy_power}</b></p><p>💰 +${d.reward} AETHER</p><p>✨ +${d.xp} XP</p><button class="primary" data-go="battle">BACK TO ARENA</button></div>`}catch(e){toast(e.message)}}
  async function claimQuest(code){try{const d=await api('/quests/claim',{method:'POST',body:JSON.stringify({code})});toast(`🎁 +${d.reward} AETHER`);state=await api('/state');renderStats();quests()}catch(e){toast(e.message)}}
  async function leaderboard(){try{const d=await api('/leaderboard');screenEl.innerHTML=`<div class="section-title"><b>🏆 Leaderboard</b></div>${d.rows.map((x,i)=>`<div class="item"><b>#${i+1} ${esc(x.username||'Player')}</b><span>⭐${x.level} · ⚡${x.power}</span></div>`).join('')}`}catch(e){toast(e.message)}}
  async function merge(){try{const d=await api('/merge/options');screenEl.innerHTML=`<div class="section-title"><b>🧬 Merge Lab</b></div><p class="muted">Combine 3 identical Beasts to evolve them.</p>${d.options.map((x,i)=>`<div class="item"><span><img src="${art(x.name)}" style="width:52px;height:52px;object-fit:cover;border-radius:10px;vertical-align:middle;margin-right:8px"> <b>${esc(x.name)}</b><small>${x.count}/3 → ${esc(x.next_name)}</small></span><button class="secondary" data-merge="${i}">MERGE</button></div>`).join('')||'<div class="empty">Collect three identical Beasts first.</div>'}`}catch(e){toast(e.message)}}
  async function doMerge(i){try{const d=await api('/merge',{method:'POST',body:JSON.stringify({index:i})});toast(`🧬 Evolved into ${d.pet.name}!`);state=await api('/state');renderStats();merge()}catch(e){toast(e.message)}}
  async function dailyScreen(){const d=state.daily||{};screenEl.innerHTML=`<div class="hero"><div class="big-number">🎁</div><h1>Daily Reward</h1><p class="muted">Current streak: <b>${d.streak||0}</b></p><button class="primary" data-action="daily">CLAIM DAILY</button></div>`}
  function profile(){const p=state.player;screenEl.innerHTML=`<div class="hero"><div class="big-number">◉</div><h1>${esc(p.username||'Beast Master')}</h1><p class="muted">Level ${p.level} · ${state.pets.length} Beasts</p><div class="grid"><div class="card"><b>🏆 Wins</b><div class="big-number">${p.wins}</div></div><div class="card"><b>⚔ Battles</b><div class="big-number">${p.battles}</div></div></div><button class="secondary" data-go="leaderboard">🏆 LEADERBOARD</button></div>`}

  async function go(s){
    setActive(s);
    try {
      if(s==='home') home(); else if(s==='pets') pets(); else if(s==='summon') summon(); else if(s==='battle') await battle(); else if(s==='profile') profile(); else if(s==='daily') await dailyScreen(); else if(s==='quests') await quests(); else if(s==='merge') await merge(); else if(s==='leaderboard') await leaderboard();
    } catch(e) { toast(e.message); }
  }

  document.addEventListener('click', async e => {
    const goBtn=e.target.closest('[data-go]'); if(goBtn){e.preventDefault();return go(goBtn.dataset.go)}
    const nav=e.target.closest('nav button'); if(nav){e.preventDefault();return go(nav.dataset.screen)}
    const s=e.target.closest('[data-summon]'); if(s)return doSummon(Number(s.dataset.summon));
    const q=e.target.closest('[data-quest]'); if(q)return claimQuest(q.dataset.quest);
    const m=e.target.closest('[data-merge]'); if(m)return doMerge(Number(m.dataset.merge));
    const f=e.target.closest('[data-fight]'); if(f)return fight();
    const d=e.target.closest('[data-action="daily"]'); if(d)return daily();
    const p=e.target.closest('[data-pet]'); if(p)return petDetails(p.dataset.pet);
    if(e.target.closest('[data-close]'))e.target.closest('.modal')?.remove();
  });

  window.go=go; window.daily=daily; window.doSummon=doSummon; window.petDetails=petDetails; window.fight=fight; window.claimQuest=claimQuest; window.doMerge=doMerge;

  window.addEventListener('error', e => showStatus(`JavaScript error: ${e.message || 'unknown error'}`, true));
  window.addEventListener('unhandledrejection', e => showStatus(`Application error: ${e.reason?.message || e.reason || 'unknown error'}`, true));

  async function boot(){
    showStatus('Connecting to AetherBeasts…');
    try {
      state=await api('/state');
      renderStats();
      await go('home');
    } catch(e) {
      showStatus(e.message || 'Unable to connect to game server.', true);
      console.error('AetherBeasts boot failed', e);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('settings')?.addEventListener('click',()=>tg?.showPopup?tg.showPopup({title:'AetherBeasts',message:'Mini App connected.',buttons:[{type:'ok'}]}):go('profile'));
    boot();
  });
})();
