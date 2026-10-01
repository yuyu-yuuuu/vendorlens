# VendorLens Review Context

VendorLens supports evidence-backed supplier selection. This glossary fixes the business language used by product, code, reports, and tests.

## Language

**Review Project（评审项目）**:
One supplier-selection workspace containing buyer requirements, candidate suppliers, submitted materials, and review settings.
_Avoid_: Workspace, case, procurement task

**Vendor（供应商）**:
A candidate organization in a Review Project. A Vendor may submit multiple documents and immutable document versions.
_Avoid_: Provider, bidder, company

**Requirement（需求项）**:
One buyer expectation that can be evaluated independently against a Vendor's response.
_Avoid_: Feature, question, criterion

**Mandatory Requirement（必选需求）**:
A Requirement whose explicit non-compliance disqualifies a Vendor regardless of scores in other domains.
_Avoid_: Important requirement, high-weight requirement

**Requirement Compliance（需求符合性）**:
The evidence-backed relationship between one Requirement and one Vendor response: met, partially met, not met, or not stated.
_Avoid_: Technical score, requirement score

**Cost Model（成本模型）**:
A normalized representation of one Vendor's one-time, recurring, optional, and total ownership costs over the Review Project's comparison period.
_Avoid_: Quote, price score, budget

**Commitment（承诺）**:
A verifiable Vendor statement about delivery dates, milestones, staffing, dependencies, service levels, acceptance, warranty, or limitations.
_Avoid_: Promise, plan item, risk

**Commitment Verification（承诺核验）**:
The evidence-backed status of a Commitment's clarity, completeness, consistency, and verifiability.
_Avoid_: Delivery score, project review

**Domain Assessment（领域评审结果）**:
The structured findings, evidence, conflicts, and missing information produced for one Vendor in one review domain. It never selects the winning Vendor.
_Avoid_: Agent answer, review report

**Degraded Assessment（降级评审结果）**:
A Domain Assessment produced only from explicit facts and deterministic rules after the corresponding domain Agent cannot return a valid result. It can support only a conditional Recommendation.
_Avoid_: General Agent fallback, guessed result

**Hard Disqualification（硬性淘汰）**:
A non-compensable result triggered by an explicit deterministic rule, such as failure of a Mandatory Requirement.
_Avoid_: Low score, recommendation penalty

**Recommendation（推荐结果）**:
The outcome calculated from validated Domain Assessments, frozen public weights, and Hard Disqualifications: recommended, conditionally recommended, or unable to recommend.
_Avoid_: Agent opinion, winner prediction
