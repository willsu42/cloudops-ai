# Knowledge corpus

This directory holds the **ordinary retrieval corpus**: documents that later
weeks will parse, chunk, and index. Everything here may be shown to a model.

Do **not** put fault-injection instructions, feature-flag scenario guides,
scenario solutions, expected root causes, or evaluator answers here. Those live
in `datasets/evaluation_private/` and are never ingested.

## `otel-demo/` — OpenTelemetry Demo documentation

| File | Source page |
| --- | --- |
| `architecture.md` | <https://opentelemetry.io/docs/demo/architecture/> |
| `docker-deployment.md` | <https://opentelemetry.io/docs/demo/docker-deployment/> |
| `service-checkout.md` | <https://opentelemetry.io/docs/demo/services/checkout/> |
| `service-cart.md` | <https://opentelemetry.io/docs/demo/services/cart/> |
| `service-payment.md` | <https://opentelemetry.io/docs/demo/services/payment/> |
| `service-shipping.md` | <https://opentelemetry.io/docs/demo/services/shipping/> |
| `service-currency.md` | <https://opentelemetry.io/docs/demo/services/currency/> |
| `service-product-catalog.md` | <https://opentelemetry.io/docs/demo/services/product-catalog/> |
| `service-email.md` | <https://opentelemetry.io/docs/demo/services/email/> |

**Attribution.** © OpenTelemetry Authors. These files are copied **unmodified**
(byte-for-byte, including Hugo front matter and shortcodes) from
[open-telemetry/opentelemetry.io](https://github.com/open-telemetry/opentelemetry.io)
at commit `145fd1194b631f2292d816d4d65d26b6c5a4aebd`, retrieved 2026-11-17.
They are licensed under
[Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
That license applies to these files only, not to the rest of this repository.
Code snippets quoted inside them come from
[open-telemetry/opentelemetry-demo](https://github.com/open-telemetry/opentelemetry-demo)
(Apache-2.0).

`manifest.json` records, per document: source URL, source file URL at the
pinned commit, title, kind, service, and whether the copy was modified. The
seed script computes the SHA-256 content hash from the local file.

### Documentation revision vs application version

The docs are not versioned per demo release; service pages link to the demo
repository's `main` branch. At retrieval time:

- docs revision: `opentelemetry.io@145fd11` (2026-11-17)
- demo `main`: `opentelemetry-demo@b825c7c` (2026-11-13)
- latest demo release tag: `3.1.0`

So the corpus describes "the demo as documented on 2026-11-17", which is close
to, but not guaranteed identical to, release 3.1.0.

### Selection

The pages cover the architecture, local Docker deployment, and the services on
the checkout path. The checkout service's direct dependencies were verified in
source, not only in the diagram: `src/checkout/main.go` at `b825c7c` requires
`SHIPPING_ADDR`, `PRODUCT_CATALOG_ADDR`, `CART_ADDR`, `CURRENCY_ADDR`,
`EMAIL_ADDR`, and `PAYMENT_ADDR`, and optionally reads `KAFKA_ADDR`. This
matches the architecture diagram, where shipping in turn calls quote.

Deliberately excluded: the Feature Flags page (it lists failure-injection
flags) and the Kubernetes deployment page (out of scope for local work). The
product-catalog page mentions that the service sets span status when a feature
flag forces an error. It is kept because that passage explains
instrumentation, not how to trigger the fault.

The quote service has a docs page that is not yet in the corpus. Add it when
shipping-quote questions need it.
