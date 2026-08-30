# 研究進度總覽(RESEARCH STATUS)— 2026-07-24 打包版

> 這份文件的目的:讓你(或任何協作的 AI)在另一台電腦上打開這個包,
> 五分鐘內恢復完整研究脈絡並接續下去。

## 一、研究綱領(north star)

**命題**:agent 工作時,context window 的內容品質決定 output 品質。真實
工作是數百個小任務堆疊在同一 session,每任務殘留的 context 雜質會複利
累積,導致注意力飄散與幻覺。**主張的解法**:以 level ≤ 3 的 B+ tree 對
codebase 建立搜尋路由表,讓 agent 在 ≥9 成情況下用最少 context 完成任務
(context SLO:每任務 context 增量有上界、與 repo 規模無關)。

方法論紀律(沿 context-render repo 的 SPIKES 慣例):每個實驗開跑前
凍結 pre-verdicts 進 SPIKES.md;verdict 一經記錄即凍結,推翻是
change-management 事件;結論用 observed shape 語言,不搞顯著性劇場
(小樣本時)。

## 二、實驗總覽與核心結論

### W6 — 路由拓撲實驗(已完結,verdict #29–#33 已凍結)

兩輪,共 1,080 場,6 個路由變體(V0 無路由 / V1 扁平有標籤 / V1b 扁平
裸路徑 / V2 B+≤3 純路由 / V3 深瘦樹 / V4 混合節點):

- **B1(meridian:11.2k 行友善 codebase)**:全變體 100% 答對;深度成本
  精確線性(actions-to-target = hop 深度);V1 行動成本最低。
- **B2(obsidian:25k 行敵對 codebase,同名誘餌 ×244、registry 間接)**:
  仍全對;V1/V1b(全域地圖)搜尋率壓低 10–40 倍,分層路由 ≈ 無路由。
- **關鍵轉折(使用者 review)**:改用 context 佔用 p90 看,排序反轉——
  純分層路由(V2/V3)尾部最緊(V2 對 V0:−3.6k tok,p=0.001;V2 對 V3
  不可分 p=0.279)。**兩種地圖哲學**:扁平 = eager loading(行動省、
  context 稅隨 repo 線性長);B+ = lazy loading(行動多、context 稅有界)。
- **「≤3」的最終論證**:不是尾部資料選的,是「lazy 類別內選最淺可覆蓋
  深度」的工程解(B1 深度線性計費 + fanout 10–20 × 3 層 = 數千檔容量)。
- 凍結 verdict:#29 成立附條件、#30 拐點不成立、#31 成立且排序對目標
  函數敏感、#32 行為面成立、#33 未測。

### W7 — context 雜訊因果實驗(已完結,verdict #34–#37 已於 2026-07-24 凍結)

rev2 設計(使用者推翻注入式初版):atlas(118k 行、payments/geo/training
三真實 domain、跨域鏈)上 agentic 導航,模糊+計算型任務(ground truth 由
執行程式碼產生),雜訊 = 注入的「session 殘留」。780 場 + 兩輪 pilot:

| arm | 殘留 | 錯誤率 |
|---|---|---|
| R0 / R15n / R15d / R45n | 無 / 中性 / 誘餌 / 中性45k | 全部 0% |
| R45d | 同名誘餌 45k | 1.5%(p=0.249) |
| Rstale | 答案檔的舊版副本 | **4.6%(p=0.0147)** |

**四個核心發現**:
1. 雜訊「量」無罪(520 場零錯)——#34 推翻。
2. 傷人的是「品質」梯度:中性 0% → 誘餌 1.5% → 陳舊 4.6%——#35 成立。
3. 全部 6 個幻覺都是逐字照抄陷阱值(零捏造)——#36 成立 6/6。
4. **(修正版,transcript 稽核過)重查無法中和陳舊副本**:所有失敗 run
   都重讀了 live 檔案,新舊兩版同在 context,綜合階段抓錯——常數衝突
   live 贏(2/20 輸),**邏輯衝突陳舊版 4/10 贏**。查證行為完好,結論
   形成被污染。——#37 成立帶此 carve-out。

**對綱領的意義**:堆疊論證最精確形式——危險的不是殘留體積,是 context
裡「答案內容的陳舊副本」(尤其陳舊邏輯)。「保持 context 乾淨」從省成本
升級為防禦必需(重查救不了你);lazy-loading 路由的核心價值 = 少載入
未來會過期的副本。

### W8 — 堆疊 session 實驗(已立骨架,未開工)

設計要點(見 W7 spec §6 + W7 發現):單一持續 session 跑 N ≥ 30 個連續
任務、context 不重置;**任務之間穿插檔案修改**(讓 Rstale 條件自然發生);
量測 context 成長曲線、後期任務錯誤漂移、compaction 事件;六個 W6 變體
對比。這是使用者論文核心主張的原生棲地。

## 三、待辦佇列(按優先序)

1. ~~凍結 #34–#37 進 SPIKES.md~~ — **已完成(2026-07-24)**:#34 不成立、
   #35 成立、#36 成立 6/6、#37 成立帶 carve-out(修正版措辭),含 §2b
   re-scope 與凍結日期註記。
2. **邏輯陷阱專門 arm(W7b)——已完結,verdict #38 已凍結(2026-07-24,
   使用者審核後)**:pilot 48 + 全量 120(reps 10→5 使用者指令)。結果:A0 0/40、
   Aconst 9/40、Alogic 10/40(稽核後);**類別級 logic>const 不對稱未複製**
   (配對 6/35 對 9/35,方向反轉),兩個預先具名推翻條件全中,效應集中於
   L08(4/5,p=0.010)→ 建議 verdict:類別主張不成立、殘餘窄幅成立;新軸線
   假說「推導自足性」(post-hoc,未註冊)。**跨實驗異常掛起**:殘留逐字節
   相同但 W7 Rstale 2/120 vs W7b 11/56,候選解釋含 arm 標籤路徑洩漏。
   報告:scripts/w8/results/W7b_report{,.zh-TW}.md。
3. **推導自足性專門 arm(W7c)——已完結,verdict #39 已凍結(2026-07-24,
   使用者審核後,成立)**:120 場。結果:A0 0/40(gate 過)、Dwork 9/40、Dself 19/40
   (稽核後)——**同突變只加一句錯誤規則散文,stale-win 率翻倍**
   (p=0.017;排除 L08 仍 p=0.015)。Dwork 重現 W7b Alogic(批次穩定);
   一眼可重算的公式題免疫(L03/L07 0/5)→「查證成本」梯度。建議
   verdict:成立。報告:scripts/w8/results/W7c_report{,.zh-TW}.md。
4. **W8 堆疊 session——已完結,verdict #40–#43 已凍結(2026-07-24,
   使用者審核後)**:60 sessions(6 變體 × 10 reps,reps 5→10 使用者指令)=
   1,800 任務回答。結果:察覺率 V1/V1b 8/10、V2 7/10、V4 1/10、
   V0/V3 0/10;突變後 58.6% 逐字舊答案,pre+ctrl 99.9%。機制:
   重訪習慣中介一切(零檔案變更提醒——agent 聲稱的 system-reminders
   是捏造);V4 同拓撲加散文即崩盤;V0 context 反而最低(#42 推翻,
   省 context 與製造陳舊是同一行為)。建議 verdict:#40 成立(最大
   效應)、#41 成立、#42 不成立帶機制改寫、#43 成立(carve-out 幾乎
   缺席)。報告:scripts/w8/results/W8_report.zh-TW.md。閘門史:
   G1 dry run 過(評分視窗 30→60)、G2 抓到 T13 遞移陳舊(排程 v2)。
5. **W8d 穩健性 arms——已完結,#46/#47 已凍結(2026-07-30,使用者
   審核後)**:23 sessions。#46 零衰減(改寫 140/140,語意層重用,
   審稿威脅 1 解除);#47 天花板級成立(一句變更提示 → 0/140,
   防禦階層改寫:訊號>地圖>無;審稿威脅 2 轉為最強實務發現)。
   報告:scripts/w8/results/W8d_report.zh-TW.md;審稿文件:
   docs/anticipated-reviews.md(威脅 1/2 DONE)。
6. **context-render 新 gauge**:偵測「讀過→被改→舊版仍在 context」狀態
   (W7 證明的唯一真實失效模式)。
7. ~~論文骨架~~ — **draft 0 完成(2026-07-29)**:docs/paper-skeleton.md
   (標題候選、摘要主張鏈、四個 study 對應 W6–W8、證據對照表
   claim↔verdict↔data、缺口清單 G-A~G-F 含建議、圖表計畫)。
   缺口 G-A/G-D 已補跑並凍結 #44/#45(2026-07-29):任何架構散文
   導言都抑制重訪(V4h 0/10);陷阱三模型通用、救援有能力門檻
   (haiku V1 0/10)。其餘缺口以限制聲明處理。報告:
   scripts/w8/results/W8bc_report.zh-TW.md。

## 四、檔案地圖(本包內)

```
SPIKES.md                     — 全部凍結的 verdicts(#1–#47)
RESEARCH_STATUS.md            — 本文件
specs/
  2026-07-21-w6-routing-topology.md   — W6 spec(含兩次 amendment)
  2026-07-23-w7-context-noise.md      — W7 spec(§0 north star、§2b rev2、§6 W8 骨架)
  2026-07-24-w7b-logic-trap.md        — W7b spec(stale-logic 不對稱專門 arm,§2c/§2d amendments)
  2026-07-24-w7c-self-sufficiency.md  — W7c spec(推導自足性專門 arm)
  2026-07-24-w8-stacked-sessions.md   — W8 spec(堆疊 session,#40–#43)
scripts/w6_phase_b/           — W6 B1(meridian)：生成器、變體建構、任務集、
                                extract_metrics.py、analyze_all.py、results/(含雙語報告)
scripts/w6_phase_b2/          — W6 B2(obsidian)：gen_codebase2.py、verify_codebase2.py、
                                build_variants2.py、tasks2.json、results/(含雙語報告)
scripts/w8/                   — atlas + W7：gen_atlas.py、verify_atlas.py、tasks3.json(棄用)、
                                tasks4.json(rev2 現行)、build_packs.py(棄用)、
                                build_residue.py(rev2 現行)、w7-rev2-full.js(跑批 workflow 腳本)、
                                results/(6 arms 全量 + pilot + 三版報告)、
                                【W7b】tasks5.json、check_w7b_mutations.py、
                                build_residue_w7b.py、w7b-run.js、results/W7b_report{,.zh-TW}.md、
                                【W7c】build_residue_w7c.py、results/W7c_report{,.zh-TW}.md
```

**注意**:三個實驗 codebase(meridian / obsidian / atlas)與 residue/packs
不在包裡——生成器全部確定性,一鍵重建:

```bash
# 各目錄下
python3 gen_codebase.py    # meridian(11,240 行,逐字元可重現)
python3 gen_codebase2.py && python3 verify_codebase2.py content/obsidian-repo
python3 gen_atlas.py && python3 verify_atlas.py content/atlas-repo
python3 build_variants.py / build_variants2.py     # W6 變體
python3 build_residue.py                            # W7 殘留(78 檔,驗證零違規)
```

## 五、實驗執行環境備忘

- 執行方式:雲端 subagent 艦隊(Claude Cowork workflow),每批一個 arm
  (120–130 場),批間立即把結果 JSON 落地到 repo(防 container 回收)。
- 模型槓桿:W6/W7 全部 sonnet + effort=medium(W7 pilot 定案;sonnet/low
  與 haiku/low 都破不了注入式設計的天花板——這是 rev2 改版的實證依據)。
- 評分:substring value_groups + 開頭 90 字元負向視窗(neg_groups)+
  justification 併入評分;經兩輪 pilot 修掉 4 個誤殺 bug;所有標記錯誤
  最後都人工覆核過。
- 已知踩坑:container 閒置會被回收(背景 workflow 陪葬)→ 分批+落地;
  workflow args 有時以字串抵達 → script 開頭要 parse guard;額度上限
  會殺 run → workflow resume(resumeFromRunId)可從 journal 快取續跑。

## 六、資料量總帳

W6:1,080 場(B1 360 + B2 720)。W7:884 場(pilot 52+26+26 + 全量 780)。
W7b:168 場(pilot 48 + 全量 120)。W7c:120 場。W8:60 sessions ×
30 題 = 1,800 任務回答(+G1 dry run 30)。合計 **2,252 場單任務 +
1,800 堆疊任務回答**,全部原始資料與 transcript 都在 results/ 裡。
