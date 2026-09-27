# Configuration precedence and customization

A setting has:setting_id,scope_type,scope_id,revision,declared_value,origin,effective_value,hard_bound,applies_to,state,review_id,updated_at. Product/account legal hard limits are intersected;lower-scoped settings can narrow permitted behavior but cannot widen it. Unset means inherit,not0,false or unlimited. Explicitly disabled is distinct from missing. Reset-to-inherit is a named operation with a diff.

Three tracks remain separate:
1.Personal view:theme,density,time zone,columns,optional panel order,filters and bookmarks. Save is local user-scoped state only.
2.Operational config:collector/account labels,notification destinations,quota profiles and source routing. Save draft;validate;review when it affects execution boundaries.
3.Financial/product config:sizing,stops,trail,target meanings,holding rules,portfolio weights,prices,mandates. Immutable released versions and explicit applicability. No 'Save' button silently activates new financial behavior.

Forms and284 field definitions are in catalog/forms.json. Their default numbers for research reflect inherited research conventions,not live approved limits. Live money/risk limits start unset unless loaded from an existing approved policy. Product draft defaults cash10000bps only as an incomplete empty composition;not published as a real investment portfolio.

Customer personalization cannot create a new strategy or alter a provider signal. Copying settings are constrained by exact account/platform/product approval. Product choice,billing entitlement,platform connection and mandate have independent state machines. Subscription upgrade does not start trades;payment failure does not cancel native protection. Marketing quiet hours do not imply safety alerts can be dropped.

Every operational change shows affected future entries,open episodes,rights,customers and external channels. Default financial-version change applies only to future entries. An existing-position adjustment has its own preview and execution recipe. Credentials are references selected from verified server metadata,not free-text exposed secrets.

Layout permissions:fixed safety block is always first;optional widgets can be reordered,hidden or reset from allowlist. Each layout has schema_version and migration from older preferences. Do not erase saved view on additive column changes. Unknown widget IDs are ignored with a visible layout reset suggestion,never used to import code. Desktop/tablet/mobile layouts independent within same role. GridStack optional but keyboard alternatives compulsory.
