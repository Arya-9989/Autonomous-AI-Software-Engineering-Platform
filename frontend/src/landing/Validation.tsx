import { CHECK_COUNT, GROUPS } from "./checks";
import { CountUp } from "./CountUp";
import { sample } from "./sample";

export function Validation() {
  return (
    <section aria-labelledby="validation-title" className="bg-surface-2 py-24 md:py-40">
      <div className="wrap">
        <div data-reveal>
          <p className="t-eyebrow text-accent-ink">Validation</p>
          <h2 id="validation-title" className="t-section mt-3 max-w-[760px] text-ink">
            Treated as untrusted. Checked before you download.
          </h2>
          <p className="t-body-lg mt-5 max-w-[720px] text-ink-2">
            Every file an agent writes is input to be verified. When a check fails with an error, the reviewer
            sends the problems back to the code generator for a bounded repair round before it writes its
            review.
          </p>
        </div>

        <ul className="mt-12 grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-reveal>
          {GROUPS.map((group) => (
            <li key={group.title} className="rounded-[20px] bg-surface p-6">
              <h3 className="text-[17px] leading-6 font-semibold text-ink">{group.title}</h3>
              <ul className="mt-3 space-y-1.5">
                {group.items.map((item) => (
                  <li key={item} className="flex gap-2.5 text-[14px] leading-5 text-ink-2">
                    <span aria-hidden className="mt-[7px] size-1.5 shrink-0 rounded-full bg-accent-ink" />
                    {item}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>

        <div className="mt-16 grid gap-10 md:grid-cols-2 md:gap-16" data-reveal>
          <div>
            <dl data-testid="validation-stats" className="grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-4">
              {[
                ["Files checked", sample.validation.files_checked],
                ["Checks", CHECK_COUNT],
                ["Errors", sample.validation.errors],
                ["Warnings", sample.validation.warnings],
              ].map(([label, value]) => (
                <div key={label} className="flex flex-col-reverse">
                  <dt className="mt-1 text-[14px] leading-5 text-ink-2">{label}</dt>
                  <dd className="text-[48px] leading-[52px] font-semibold tracking-[-0.01em] text-ink tabular-nums md:text-[56px] md:leading-[60px]">
                    <CountUp value={Number(value)} />
                  </dd>
                </div>
              ))}
            </dl>
            <p className="mt-4 text-[14px] leading-5 text-ink-2">
              The library example, as validated by the reviewer.
            </p>
          </div>
          <div className="md:pt-3">
            <h3 className="text-[21px] leading-[25px] font-semibold tracking-[0.011em] text-ink">
              Proven in CI.
            </h3>
            <p className="t-body mt-3 text-ink-2">
              On every pull request and every push to main, this repository’s CI generates the library project
              with the offline provider, runs its own ruff and pytest, runs{" "}
              <span className="font-mono text-[15px]">terraform validate</span> on its Terraform and{" "}
              <span className="font-mono text-[15px]">kubeconform</span> on its manifests.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
