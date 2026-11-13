import {useState} from 'react';
import {useAction} from '../hooks/useAction';
import {runScenario} from '../api/runs';
import {BootstrapSummary,LotResult,Result} from '../types/domain';
import {number,percent} from '../utils/format';
import {DataTable} from './common/DataTable';

const statusLabel={complete:'完整',partial:'部分成功',failed:'全部失败'};

function Conditions({summary}:{summary:BootstrapSummary}){
  return <div className="bootstrap-meta">
    <div><span>种子</span><b>{summary.seed}</b><small>有放回、按批次编号排序后抽样</small></div>
    <div><span>请求次数 / 成功 / 失败</span><b>{summary.requested_reps}<em> / {summary.successful_reps} / {summary.failed_reps}</em></b><small>每次重抽样都重新拟合 α、β</small></div>
    <div><span>混合权重</span><b>1 / {summary.successful_reps || '—'}</b><small>仅成功重拟合样本等权参与混合</small></div>
    <div><span>拟合来源</span><b>{summary.scenario_reused?'情景复用':summary.refit_reused?(summary.reuse_source==='cache'?'缓存复用':'源运行复用'):'本次重算'}</b><small>拟合键 {summary.fit_hash.slice(0,12)}…</small></div>
  </div>;
}

export function BootstrapPanel({result,currentLot,runId}:{result:Result;currentLot:LotResult;runId:number}){
  const b=result.bootstrap;
  const a=useAction();
  const [preview,setPreview]=useState<Result|null>(null);
  const [threshold,setThreshold]=useState(result.threshold);
  const [future,setFuture]=useState(result.future_n);
  if(!b)return <section className="bootstrap-panel legacy">
    <div className="section-heading"><h2>批次重抽样</h2><span>本运行没有记录重抽样种子与次数</span></div>
    <p className="caption">此研究只保存条件于点估先验的结果；旧数据保持原样，可基于同一冻结快照发起新的重抽样研究。</p>
  </section>;
  const shown=(preview?.bootstrap)||b;
  const conditionalRow=(preview||result).lots.find(x=>x.lot.code===currentLot.lot.code)||currentLot;
  const lot=shown.lots.find(x=>x.lot.code===currentLot.lot.code);
  return <section className="bootstrap-panel">
    <div className="section-heading">
      <h2>批次重抽样 · 等权混合</h2>
      <span>种子 {shown.seed} / {shown.requested_reps} 次 / 状态 {statusLabel[shown.status]}</span>
    </div>
    <div className={'bootstrap-banner '+shown.status}>
      <b>{statusLabel[shown.status]}重抽样</b>
      <p>{shown.notes.interpretation}</p>
      <p>{shown.notes.partial}</p>
      {shown.failed_reps>0&&<p className="failure-note">失败重拟合样本 {shown.failed_reps} 个，原因逐条保留在下方；它们不进入等权混合，页面不把部分成功当作完整 {shown.requested_reps} 次结果。</p>}
      {preview&&<p className="failure-note">当前显示情景切换后的临时结果：阈值 {shown.threshold}、未来 {shown.future_n} 粒；重拟合复用原运行，未重新抽样，也未写回研究档案。</p>}
    </div>
    <Conditions summary={shown}/>
    <div className="mixture-compare">
      <div className="conditional-box">
        <span className="eyebrow">CONDITIONAL / 条件于点估先验</span>
        <h3>{conditionalRow.lot.code} · 原条件结果</h3>
        <dl>
          <div><dt>条件均值</dt><dd>{percent(conditionalRow.mean)}</dd></div>
          <div><dt>95% 条件等尾</dt><dd className="small">{number(conditionalRow.lower,4)}—{number(conditionalRow.upper,4)}</dd></div>
          <div><dt>条件 Pr(p&gt;{conditionalRow.threshold})</dt><dd>{number(conditionalRow.prob_gt_threshold,5)}</dd></div>
          <div><dt>条件未来均值</dt><dd>{number(conditionalRow.prediction.mean,3)}</dd></div>
        </dl>
        <small>单次点估 αβ 下的区间；不含先验估计误差，不能回答“换一批批次样本会怎样”。{preview&&' 当前条件值已按新情景重算，αβ仍沿用原拟合。'}</small>
      </div>
      <div className="mixture-box">
        <span className="eyebrow">BOOTSTRAP MIXTURE / 混合成功重拟合</span>
        <h3>{currentLot.lot.code} · 重抽样混合</h3>
        {lot?<dl>
          <div><dt>混合均值</dt><dd>{percent(lot.mixture_mean)}</dd></div>
          <div><dt>95% 混合区间</dt><dd className="small">{number(lot.mixture_lower,4)}—{number(lot.mixture_upper,4)}</dd></div>
          <div><dt>混合 Pr(p&gt;{shown.threshold})</dt><dd>{number(lot.mixture_prob_gt_threshold,5)}</dd></div>
          <div><dt>混合未来均值</dt><dd>{number(lot.prediction.mean,3)}</dd></div>
          <div><dt>跨样本均值范围</dt><dd className="small">{number(lot.replicate_spread.mean_q025,4)}—{number(lot.replicate_spread.mean_q975,4)}</dd></div>
          <div><dt>未来计数 95%</dt><dd className="small">{lot.prediction.lower_k}—{lot.prediction.upper_k} 粒</dd></div>
        </dl>:<p className="empty">全部重拟合失败，未生成该批混合结果。</p>}
        <small>成功重拟合批次的等权混合；包含重抽样下先验重估的波动，但不是频率覆盖保证。跨样本均值范围只是成功样本之间的离散范围，不是置信区间。</small>
      </div>
    </div>
    {shown.status==='failed'?
      <p className="notice">全部重拟合失败：仅保留条件结果与失败原因，不输出混合区间、阈值概率或未来预测。请检查批次计数边界后改用新种子或更多批次。</p>:
      <div className="compact-table">
        <DataTable head={['批次','k / n','混合均值','混合q025','混合q975','混合Pr(p>阈值)','未来均值','未来方差','未来95%计数']}>
          {shown.lots.map(x=><tr key={x.lot.code} className={x.lot.code===currentLot.lot.code?'selected':''}>
            <td>{x.lot.code}</td><td>{x.count.k} / {x.count.n}</td><td>{number(x.mixture_mean,5)}</td><td>{number(x.mixture_lower,5)}</td><td>{number(x.mixture_upper,5)}</td><td>{number(x.mixture_prob_gt_threshold,5)}</td><td>{number(x.prediction.mean,3)}</td><td>{number(x.prediction.variance,3)}</td><td>{x.prediction.lower_k}—{x.prediction.upper_k}</td>
          </tr>)}
        </DataTable>
      </div>}
    <div className="failure-table">
      <h3>失败重拟合样本</h3>
      {shown.failures.length?<>
        <div className="failure-summary">{shown.failure_summary.map(x=><span key={x.reason}>{x.count}× {x.reason}</span>)}</div>
        <div className="compact-table"><DataTable head={['序号','原因','样本Σn','样本Σk','不同批次数']}>
          {shown.failures.map(x=><tr key={x.replicate}><td>#{x.replicate}</td><td>{x.reason}</td><td>{x.sum_n}</td><td>{x.sum_k}</td><td>{x.unique_lots}</td></tr>)}
        </DataTable></div>
      </>:<p className="caption">本次所有重拟合样本均成功；仍不把模型内混合当成频率覆盖保证。</p>}
    </div>
    <div className="scenario-switch">
      <h3>情景切换（复用重拟合，不重新抽样）</h3>
      <div className="form-row">
        <label>研究阈值<input aria-label="情景阈值" type="number" min="0.001" max="0.999" step="0.001" value={threshold} onChange={e=>setThreshold(Number(e.target.value))}/></label>
        <label>未来同批试种数<input aria-label="情景未来试种数" type="number" min="1" max="200" step="1" value={future} onChange={e=>setFuture(Number(e.target.value))}/></label>
      </div>
      <div className="scenario-actions">
        <button className="outline" disabled={a.busy} onClick={()=>a.execute(async()=>setPreview(await runScenario(runId,{threshold,future_n:future})))}>在当前运行上试算情景</button>
        {preview&&<button className="text-link" onClick={()=>setPreview(null)}>回到原情景</button>}
      </div>
      {a.error&&<div role="alert" className="notice">{a.error}</div>}
      <small>同一种子与批次数据只拟合一次；阈值和未来 m 不参与先验拟合。源数据更新导致数据哈希变化时，原拟合不会被悄悄复用。</small>
    </div>
  </section>;
}
