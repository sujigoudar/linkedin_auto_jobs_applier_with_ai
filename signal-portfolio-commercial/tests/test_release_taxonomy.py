"""Track 34 -- `app.services.release_taxonomy.compute_release_stage`'s
own unit tests. Pure function over `Product.lifecycle_state`, so these
need no database at all beyond constructing a `Product` instance (same
discipline as the rest of this suite: no DB fixture is pulled in where
a real one isn't needed)."""
from app.models.product import Product, ProductLifecycleState
from app.services.release_taxonomy import ReleaseStage, compute_release_stage


def _product(lifecycle_state: ProductLifecycleState) -> Product:
    return Product(
        tenant_id="tenant-a",
        product_name="Test",
        slug="test-product",
        lifecycle_state=lifecycle_state,
    )


def test_draft_maps_to_research_only():
    assert compute_release_stage(_product(ProductLifecycleState.DRAFT)) == ReleaseStage.RESEARCH_ONLY


def test_validated_maps_to_shadow():
    assert compute_release_stage(_product(ProductLifecycleState.VALIDATED)) == ReleaseStage.SHADOW


def test_approved_maps_to_limited_live():
    assert compute_release_stage(_product(ProductLifecycleState.APPROVED)) == ReleaseStage.LIMITED_LIVE


def test_published_maps_to_fully_released():
    assert compute_release_stage(_product(ProductLifecycleState.PUBLISHED)) == ReleaseStage.FULLY_RELEASED


def test_mapping_is_total_over_every_lifecycle_state():
    """Every real `ProductLifecycleState` member has a mapped stage --
    this is what keeps `compute_release_stage` from ever needing to
    silently fall back to a fabricated default."""
    for state in ProductLifecycleState:
        assert isinstance(compute_release_stage(_product(state)), ReleaseStage)
