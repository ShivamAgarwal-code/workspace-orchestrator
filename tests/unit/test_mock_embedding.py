import math

from app.llm.mock_provider import MockEmbeddingProvider


async def test_deterministic_same_text_same_vector():
    provider = MockEmbeddingProvider(dimension=256)
    v1 = await provider.embed_one("Turkish Airlines booking confirmation")
    v2 = await provider.embed_one("Turkish Airlines booking confirmation")
    assert v1 == v2


async def test_unit_normalized():
    provider = MockEmbeddingProvider(dimension=256)
    vec = await provider.embed_one("some text to embed")
    norm = math.sqrt(sum(v * v for v in vec))
    assert math.isclose(norm, 1.0, rel_tol=1e-6)


async def test_similar_text_scores_higher_than_unrelated_text():
    provider = MockEmbeddingProvider(dimension=512)
    query = await provider.embed_one("Turkish Airlines flight booking cancellation")
    related = await provider.embed_one("Your Turkish Airlines booking confirmation TK1234")
    unrelated = await provider.embed_one("Weekly industry digest newsletter cloud spending")

    def cosine(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))  # already unit-normalized

    assert cosine(query, related) > cosine(query, unrelated)


async def test_batch_embed_matches_individual_embed():
    provider = MockEmbeddingProvider(dimension=128)
    texts = ["alpha beta", "gamma delta"]
    batch = await provider.embed(texts)
    individual = [await provider.embed_one(t) for t in texts]
    assert batch == individual


async def test_empty_text_returns_zero_vector_without_error():
    provider = MockEmbeddingProvider(dimension=64)
    vec = await provider.embed_one("")
    assert len(vec) == 64
    assert all(v == 0.0 for v in vec)
