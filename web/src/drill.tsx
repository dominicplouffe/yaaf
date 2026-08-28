import { RehearsalReport } from "./api";

/** The fire drill's report, as a chairman reads it.

    Ordered the way the question comes: did it work, where did it stop, how
    often will it need me, what will it cost, what paces it — and last, in
    plain sight rather than in a footnote, what the drill is not entitled to
    claim. A caveat nobody reads is a caveat that does not exist, so it renders
    at the bottom of the panel and not in a tooltip.

    Nothing here is styled as an alarm. A drill that finds a problem has done
    its job perfectly, and a red panel would train a chairman to dread pressing
    the button that exists to save him money. */

function Walk({ report }: { report: RehearsalReport }) {
  return (
    <ol className="drill-walk">
      {report.walk.map((step) => (
        <li key={step.key} className={step.na ? "na" : step.ok ? "ok" : "miss"}>
          <span className="dm" aria-hidden="true">
            {step.na ? "–" : step.ok ? "✓" : "·"}
          </span>
          <span className="dl">{step.label}</span>
          <span className="dd">
            {step.na ? "not in this project's shape" : step.detail}
          </span>
          {step.tick !== null && !step.na && (
            <span className="dt mono">beat {step.tick}</span>
          )}
        </li>
      ))}
    </ol>
  );
}

function Money({ report }: { report: RehearsalReport }) {
  const money = report.money;
  const plan = report.plan;
  if (!money?.cap) return null;
  const unit = money.unit === "usd" ? "$" : "";
  if (!plan?.requirements) {
    return (
      <p className="help">
        An Explore project has no requirements to estimate, so there is no plan
        total to price — its shape is bets and evidence, inside a cap of{" "}
        {unit}
        {money.cap.toLocaleString()}.
      </p>
    );
  }
  return (
    <p className="help">
      The plan is <b>{plan.requirements}</b> requirement
      {plan.requirements === 1 ? "" : "s"}, <b>{plan.estimated_runs}</b> estimated run
      {plan.estimated_runs === 1 ? "" : "s"}.{" "}
      {money.usd_per_run ? (
        <>
          At {money.source}, {unit}
          {money.usd_per_run}/run that is <b>
            {unit}
            {money.total?.toLocaleString()}
          </b>{" "}
          of a {unit}
          {money.cap.toLocaleString()} cap — {money.fits ? "it fits" : "it does not fit"}.
        </>
      ) : money.break_even_usd_per_run ? (
        <>
          This box has never priced a run, so it cannot tell you the total. The plan
          fits your {unit}
          {money.cap.toLocaleString()} cap if a run costs under{" "}
          <b>
            {unit}
            {money.break_even_usd_per_run}
          </b>{" "}
          — check that against a bill you have already seen.
        </>
      ) : (
        money.why
      )}
    </p>
  );
}

export default function Drill({ report }: { report: RehearsalReport }) {
  const waits = report.interventions.reduce((n, row) => n + row.count, 0);
  return (
    <div className="drill">
      <div className={`drill-verdict ${report.ok ? "ok" : "miss"}`}>{report.verdict}</div>

      <Walk report={report} />

      {report.findings && report.findings.length > 0 && (
        <>
          <div className="drill-h">What the shape of this project means</div>
          <ul className="drill-list">
            {report.findings.map((finding, i) => (
              <li key={i}>{finding}</li>
            ))}
          </ul>
        </>
      )}

      <div className="drill-h">
        {waits > 0
          ? `It stopped and waited for you ${waits} time${waits === 1 ? "" : "s"}`
          : "It never had to wait for you"}
      </div>
      {waits > 0 && (
        <>
          <ul className="drill-list">
            {report.interventions.map((row) => (
              <li key={row.kind}>
                <b>
                  {row.count}× {row.kind}
                </b>
                {row.examples.length > 0 && (
                  <span className="help"> — {row.examples[0]}</span>
                )}
              </li>
            ))}
          </ul>
          <p className="help">
            Your autonomy dial decides which of these park. Every one is a morning
            the project is idle until you open the app.
          </p>
        </>
      )}

      {report.refusals.length > 0 && (
        <>
          <div className="drill-h">Walls it hit</div>
          <ul className="drill-list">
            {report.refusals.map((row, i) => (
              <li key={i}>
                <b className="mono">
                  {row.count}× {row.path}
                </b>
                <div className="help">{row.error}</div>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="drill-h">What it will cost, and what paces it</div>
      <Money report={report} />
      {report.capacity?.why && (
        <p className="help">
          {report.capacity.feasible ? "It fits" : "It does not fit"} —{" "}
          {report.capacity.why}.
          {report.capacity.chain_hours > 0 && (
            <>
              {" "}
              The longest serial chain is {report.capacity.chain_hours}h: one agent's
              work cannot overlap itself, whatever the project-wide ceilings allow.
            </>
          )}
        </p>
      )}

      <div className="drill-h">What this drill is not</div>
      <p className="help">{report.caveat}</p>
      <ul className="drill-list quiet">
        {report.not_exercised.map((line, i) => (
          <li key={i} className="help">
            {line}
          </li>
        ))}
      </ul>
    </div>
  );
}
