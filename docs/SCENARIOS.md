# Scenarios: how the stress test works

Type what might happen ("war with Iran", "oil drops", "US bonds fall", "Apple misses earnings").
The page shows, for every portfolio and the whole family, what gains, what loses and by how much,
with a range, the past events that looked like it, and ideas for protection.

## Approach (what professional risk systems do)

1. **Scenario → a few shocks.** A sentence becomes a short list of shocks to market factors
   (proxied by liquid ETFs: SPY market, QQQ tech, SOXX semis, IWM small caps, TLT long bonds,
   HYG credit, USO oil, XLE energy, GLD gold, UUP dollar, ITA defense, EIS Israel, ^VIX, sector
   SPDRs) or to single stocks. Shocks are shown and editable before anything is computed.
   Keeping the list short is a best practice: more shocks add noise, not information.[^shock]

2. **"Severe but plausible" sizes.** With no number given, a shock's size is calibrated from that
   factor's own history: a strong week (the 90th percentile of absolute weekly moves over ten
   years). Themes like "war with Iran" take their shocks from the median moves of past events of
   that kind (Israel–Iran June 2025, the 2024 Iranian attacks, October 2023, Ukraine 2022).[^msci]

3. **Correlated propagation.** Everything not shocked moves by its conditional expectation given
   the shocks: E[r | s] = Σ_rS Σ_SS⁻¹ s, from the joint covariance of weekly returns of factors and
   holdings. This is the "correlated stress test" of factor-model risk systems.[^msci] The same
   algebra gives the conditional variance, so every result has a range (about 2 in 3 outcomes).

4. **Calm and stressed correlations.** Correlations rise in sell-offs, so a covariance from
   normal weeks understates crisis losses.[^msci] The engine estimates two matrices, all weeks
   (exponentially weighted, one-year half-life) and stress weeks only (the worst 15% of S&P weeks),
   and shows both.

5. **Plausibility check.** The combination of shocks is scored by its Mahalanobis distance under
   the covariance. Combinations that history says are near impossible are flagged.[^shock]

6. **Historical replay.** Each matching past event is replayed on today's holdings: actual
   returns where the holding traded then, otherwise estimated from that event's factor moves.

7. **Reverse stress test.** "How big would this shock need to be to cost 10%?"[^msci]

8. **Model check.** For past events, the model is fitted only on data before the event, fed that
   event's actual factor moves, and its predictions are compared with what holdings really did.
   The page shows the hit rate and typical error so you can judge how far to trust it.

9. **Protection ideas.** The biggest contributors to the loss, what trimming each would save, and
   the index position that would offset most of the hit (sizes from the same model). Ideas to
   weigh, not advice.

## Limits

Linear model, weekly horizon, no options. History cannot contain every future event: a scenario
outside past experience is shown with a low plausibility score rather than a confident number.

[^msci]: MSCI, *Stress Testing in the Investment Process* (2010): correlated stress tests, severe
but plausible scenarios, historical vs hypothetical, reverse stress tests, crisis correlations.
[^shock]: MSCI, *Stress Testing Portfolios: Best Practices for Shockwave Propagation* (2016): check
the compatibility of shocks with the covariance, choose the estimation window, limit the number of shocks.
NBIM (Norges Bank Investment Management), *Stress testing* (2023): historical and hypothetical (predictive) stress testing.
