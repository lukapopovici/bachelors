import hashlib

from fastembed import TextEmbedding

from src.cache import cache_get, cache_set

embedder = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")


def build_embedding_text(tags: dict) -> str:
    parts = [
        f"Modality: {tags.get('Modality', '')}",
        f"Description: {tags.get('StudyDescription', '')}",
        f"Comments: {tags.get('ImageComments', '')}",
        f"BodyPart: {tags.get('BodyPartExamined', '')}",
        f"Reason: {tags.get('ReasonForTheRequestedProcedure', '')}",
    ]
    return " | ".join(part for part in parts if part.split(": ")[1])


def compute_embedding(text: str) -> list[float]:
    cache_key = f"embedding:{hashlib.md5(text.encode('utf-8')).hexdigest()}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    embedding = list(embedder.embed([text]))[0].tolist()
    cache_set(cache_key, embedding, 3600)
    return embedding
