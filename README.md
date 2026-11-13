# 铎岑种子萌发批次概率册

以独立批次的萌发数k/试种数n拟合有界Beta-Binomial经验贝叶斯先验，读取条件收缩、区间、阈值概率和同批下一轮计数预测。

铎岑种质试验室有14位批次分析员。上游 `duocen-germination-lots-202611.csv` 是各批独立试种汇总；过去直接比较小批0/1比例、忽略批间差异、把拟合先验当成已知真相。本系统替代跨批率估计与补种研究归档，下游由种质负责人批准独立补种计划，阅读者下载获批汇总/预测PMF。全部是合成研究，不执行真实补种、种子合格判定或自动发货。

## 启动

```sh
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```

可在含中文和空格的路径运行。前端 http://localhost:18236 ，API文档 http://localhost:19236/docs ，PostgreSQL localhost:17736。恰好frontend/backend/worker/db/redis五服务，均有健康检查；后端与worker等待数据库/Redis、前端等待后端。Nginx `/api/` 去前缀代理。数据库、Redis队列与backend/worker共享完整结果JSON分别使用命名卷。

| 角色 | 账号 | 密码 | 页面与权限 |
|---|---|---|---|
| admin 种质负责人 | admin@duocen.demo | Duocen2026! | 六页；批注、创建研究、提交、批准/退回 |
| analyst 批次分析员 | analyst@duocen.demo | Duocen2026! | 六页；核对n/k、创建研究、计数批注、补种说明 |
| viewer 抽样阅读者 | viewer@duocen.demo | Duocen2026! | 六页只读；获批后下载汇总及完整预测PMF CSV |

JWT角色后端真实执行，viewer写入403、analyst审批403、未批准导出403，已审阅建议不可重复审批。登录/计数批注/排队/worker完成/建议和审批写审计；登录IP每分钟最多30次。另有全局异常中间件，422校验handler只返回loc/msg文本，不回显NaN/Inf input，避免错误响应序列化成500。部署前修改JWT_SECRET及Compose内数据库口令。

## 一天的研究剧本

1. 09:00 许禾登录，打开 `DC-01`“异质批次 · 萌发观察”，核对28个独立批次，每批一条n/k，n20–180。
2. 09:05 选 `DC-01-L01`，看到58/68，原始率85.3%；给计数保留下一轮独立试种批注。
3. 09:10 查看 `DC-03` 全零边界，系统拒绝估计先验；全一或少于8批也拒绝。
4. 09:15 新建“异质批次条件EB”，研究阈值.7、同批下一轮20粒。形状界[.01,5000]与10个起点固定，阈值和未来m不参与先验拟合。
5. 09:16 worker实际多起点优化边际似然。真实主运行得到α≈8.510929815、β≈3.425220221，10/10起点通过检查，投影梯度范数约6.55e−10；这是测试起点中的最佳合格结果，不是全局最优证明。
6. 09:20 L01条件后验均值约83.2%，95%等尾区间约.7433–.9051、数据权重约85.1%；同批未来20粒条件均值约16.641、方差约3.451。
7. 09:22 同一研究再选“批次有放回重抽样”，种子20261113、200次；worker每次重拟合αβ，给出L01等权混合区间、混合Pr(p>.7)与未来计数混合PMF，并标记完整/部分成功。
8. 09:25 切换 `DC-02` 同观测率夹具：所有批70/100，α真实触及5000上界，界面标弱识别；不把窄条件区间当成稳定已知先验。
9. 10:00 陆岑批准独立补种研究，温桥下载条件汇总、条件预测PMF或重抽样混合CSV，按相同n/k口径另做独立试种。

## 六页、五实体与seed

批次总览、计数来源、先验与情景、条件后验、补种审阅、研究留痕。`GerminationStudy / SeedLot / LotCount / BayesRun / BootstrapFit / SamplingNote` 均贯穿表/model/schema/service/route/前端API/store/页面或业务组件，另有User/Audit。批次身份与n/k只读核对，计数批注可写，运行冻结快照后不回写。`BootstrapFit`只存同数据哈希+种子+次数的重拟合形状与失败记录，供情景切换复用；不参与条件后验计算。

三研究×28=84批次、84计数：DC-01潜在率Beta(8,4)再Binomial抽样（固定种子320）；DC-02全部70/100为人工构造的同观测率上界夹具，不冒充随机抽样；DC-03固定种子322生成n、k全部0，全一另外有独立算法拒绝验证。每批n20–180。自然键email、study code、(study,lot code)、lot_id唯一计数、基线run name；重复seed只补缺失，不覆盖用户批注、计数或已批准建议。两条基线运行分别演示条件EB与种子20261113/200次的重抽样混合。`SEED_DEMO=true`启动执行，失败记日志；可独立重复执行seed。

共享RunSummary在首页/结果/审阅复用，ShrinkageChart在首页/结果复用；另有StudySelector、PosteriorChart、PredictiveChart及通用Notice/DataTable/StatusBadge。useResearch/useAction处理公共状态。全部数值、CDF与PMF在Python计算，SVG只映射真实结果。

## 有界多起点边际拟合

工作模型：各批 `k_j | p_j ~ Binomial(n_j,p_j)`，批率独立 `p_j ~ Beta(α,β)`。只把它作为研究模型，不当已验证种子性能真相。各批一条完整整数计数，1≤n≤100000、0≤k≤n，不接受布尔/小数，拟合8–200批；全部0或全部1拒绝估计先验。

```text
log L = Σ[log C(n,k) + log B(α+k, β+n−k) − log B(α,β)]
x=log α, y=log β，各自界为log(.01)..log(5000)
∂logL/∂x = α Σ[ψ(k+α)−ψ(n+α+β)−ψ(α)+ψ(α+β)]
∂logL/∂y = β Σ[ψ(n−k+β)−ψ(n+α+β)−ψ(β)+ψ(α+β)]
```

Beta-Binomial PMF口径见[SciPy官方文档](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.betabinom.html)。代码使用betaln/gammaln与解析log形状梯度，最小化负logL。六个固定起点(1,1)/(2,2)/(8,4)/(.2,.2)/(1,8)/(8,1)，另四个按平滑合并率仅用于初始化、总浓度2/20/200/5000的起点；初始化平滑不加入似然伪数据。

L-BFGS-B设置maxiter2000/maxls50/gtol1e−8/ftol1e−13，全部起点保存初末形状、logL、状态、迭代/评估、原始目标梯度与投影梯度。只有优化器成功、目标/梯度有限且投影∞范数≤1e−4才接受，再选接受点中LL最高。边界的不可行下降方向投影为0，与[L-BFGS-B官方停止定义](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.fmin_l_bfgs_b.html)一致；投影为0不消除贴边弱识别。形状接近边界或更高目标候选未收敛时标弱识别，绝不声称全局最优或稳定已知先验。物理报告形状最后裁到[.01,5000]，避免exp(log5000)的机器舍入超出数个ULP。

当前DC-02真实拟合α=5000、β≈2143.357，5/10起点合格、alpha:upper，弱识别警示可见。原始负LL logshape梯度α方向非零而边界投影为0，诊断同时保留。

## 条件后验与同批预测

条件于点估αβ，单批k/n的后验为 `Beta(a=α+k,b=β+n−k)`，均值a/(a+b)，95%等尾分位[.025,.975]，阈值尾概率用Beta SF计算Pr(p>阈值)。收缩数据权重n/(n+α+β)，先验权重为其余；估计先验均值不是简单合并比例。

同批下一轮m粒（1–200）的计数为Beta-Binomial(m,a,b)：

```text
P(K=j) = C(m,j) B(a+j,b+m−j)/B(a,b), j=0..m
E[K] = m*a/(a+b)
Var[K] = m*a*b*(a+b+m)/((a+b)^2*(a+b+1))
```

用log PMF与logsumexp覆盖全部0..m，保存归一前质量/误差；只有误差≤1e−7才对浮点微差归一。CDF存201个概率点，避免直接密度端点可能无界；它是展示网格，区间与尾概率由分布函数实算。阈值/未来m变化不改变先验拟合，输入hash变化但fit_hash不变。

这些区间、阈值概率和未来计数**都条件于点估超参数**，不包含αβ估计误差，不是完整层级贝叶斯区间、频率覆盖保证、种子合格概率或自动发货依据。估计先验使用这些批次（包含本批），不冒充独立已知先验；未来预测针对同一批次，不是全新未知批。

## 批次有放回重抽样

条件口径把αβ当作已知，无法回答“换一批批次样本，估计会怎样”。重抽样研究在同一冻结快照上按批次编号排序后有放回抽取同样数量的批次，每次用同一有界多起点算法重新拟合先验；种子与次数随运行冻结记录，研究可复现。

```text
for r = 1..R:  以种子 s 从 m 个批次有放回抽 m 个 (n_j,k_j)
               α_r,β_r ← 有界多起点边际MLE（同一套检查）
成功样本 S 个，等权混合：F(p|k,n) = (1/S) Σ_r BetaCDF(p; α_r+k, β_r+n−k)
区间 [q.025,q.975] 由混合CDF二分求解；阈值概率与未来计数PMF同样逐样本计算后等权平均
```

- 种子为0–2^32−1整数，次数20–1000；抽样只依赖按批次编号排序后的数据、种子与次数，所以同种子换批次输入顺序仍得到相同样本和相同拟合。
- 每次重拟合沿用10起点、收敛/投影梯度检查与弱识别标记；抽到全部0或全部1、少于8个不同批次等退化样本时，该次记为失败并保存原因、样本Σn/Σk与不同批次数。
- 成功样本才进入混合；`complete/partial/failed`分别表示全部成功/部分成功/全部失败。部分成功页面明示“不冒充完整R次结果”，全部失败则不输出混合区间、阈值概率和未来预测，只保留条件结果与原因。
- 混合区间与阈值概率包含重抽样下先验重估的波动，仍**不是**频率覆盖保证、种子合格保证或自动发货依据；跨样本均值范围仅显示成功样本之间的离散程度。
- 情景切换（阈值/未来m）复用已冻结的重拟合，只重算条件行与混合汇总，不重新抽样；原条件结果始终保留，页面把“条件于单次点估”与“混合成功重拟合”分栏解释。
- 重拟合缓存键 = 版本+批次数据哈希+种子+次数。源数据n/k更新后数据哈希变化，旧缓存不会被悄悄复用，运行会重算；冻结快照不被源数据回写。
- `POST /runs/{id}/resample`按源运行快照发起新的重抽样；`POST /runs/{id}/scenario`只读复用不写档案；`GET /runs/{id}/bootstrap-cache`查看缓存条目；导出增加`kind=bootstrap`。旧运行没有种子/次数时字段为空，页面显示“本运行没有记录重抽样种子与次数”，条件结果照常可读；启动时`ensure_schema`为旧库补列。

## 开发与工程

React18.3/ReactRouter7.18.5/TypeScript5.7/Vite6.4.6；Python3.11/FastAPI0.115/SQLAlchemy2/NumPy2.2/SciPy1.15；PostgreSQL16/Redis7/Nginx1.27。

```sh
# backend，先准备PostgreSQL/Redis并设置DATABASE_URL、REDIS_URL
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8000
# 另开终端在backend启动worker；需可写/artifacts，推荐Compose
python -m src.worker
# 前端从项目根进入frontend，Vite代理/api到localhost:8000
cd ../frontend
npm ci
npm run dev
```

backend/src/{routes,controllers,services,models,schemas,middlewares,utils,constants,config}按来源、边际拟合、后验、运行、审阅拆分；backend/scripts/seed_demo.py；migration为可选索引，metadata首次建表，非独立全量DDL。frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}为React应用。

角色枚举backend/constants/roles与frontend/constants/enums用于鉴权/显示，队列状态backend/constants/status与前端RunStatus用于worker/StatusBadge，审阅Literal在backend/schemas/note与model/service/前端ReviewStatus。环境变量COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、JWT_SECRET、SEED_DEMO在.env；DATABASE_URL/REDIS_URL由Compose定义。

测试脚本、真实截图、关键JSON/CSV与已知差异在同批test-artifacts/gb-0032/TESTING.md，源码不放日志或截图。当前单worker，无来源上传、崩溃恢复、多worker去重或设备操作。清理仅本题：`docker compose down -v --remove-orphans`。MIT License。
