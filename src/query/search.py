from abc import ABC, abstractmethod

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.database import DicomStudyRecord
from src.query.embeddings import compute_embedding


class SearchStrategy(ABC):
    @abstractmethod
    def search(self, db: Session, query: str, limit: int,
               modality: str = None) -> list[dict]:
        """Return matching indexed study records."""
        ...

    @staticmethod
    def _record_to_dict(record: DicomStudyRecord, extra: dict = None) -> dict:
        result = {
            "id": record.id,
            "orthanc_study_id": record.orthanc_study_id,
            "patient_id": record.patient_id,
            "modality": record.modality,
            "study_date": record.study_date,
            "study_description": record.study_description,
            "image_comments": record.image_comments,
            "instance_count": record.instance_count,
        }
        if extra:
            result.update(extra)
        return result


class CosineSearchStrategy(SearchStrategy):
    def search(self, db: Session, query: str, limit: int,
               modality: str = None) -> list[dict]:
        query_vector = "[" + ",".join(str(value) for value in compute_embedding(query)) + "]"
        sql = text("""
            SELECT id, 1 - (embedding <=> CAST(:qvec AS vector)) AS similarity
            FROM dicom_studies
            WHERE embedding IS NOT NULL
            {modality_filter}
            ORDER BY embedding <=> CAST(:qvec AS vector)
            LIMIT :lim
        """.format(modality_filter="AND modality = :mod" if modality else ""))
        params = {"qvec": query_vector, "lim": limit}
        if modality:
            params["mod"] = modality.upper()
        rows = db.execute(sql, params).fetchall()
        scores = {row.id: row.similarity for row in rows}
        records = db.query(DicomStudyRecord).filter(
            DicomStudyRecord.id.in_(scores.keys())
        ).all()
        records.sort(key=lambda record: scores[record.id], reverse=True)
        return [self._record_to_dict(record, {"similarity": round(scores[record.id], 4)})
                for record in records]


class EuclideanSearchStrategy(SearchStrategy):
    def search(self, db: Session, query: str, limit: int,
               modality: str = None) -> list[dict]:
        query_vector = "[" + ",".join(str(value) for value in compute_embedding(query)) + "]"
        sql = text("""
            SELECT id, (embedding <-> CAST(:qvec AS vector)) AS distance
            FROM dicom_studies
            WHERE embedding IS NOT NULL
            {modality_filter}
            ORDER BY embedding <-> CAST(:qvec AS vector)
            LIMIT :lim
        """.format(modality_filter="AND modality = :mod" if modality else ""))
        params = {"qvec": query_vector, "lim": limit}
        if modality:
            params["mod"] = modality.upper()
        rows = db.execute(sql, params).fetchall()
        distances = {row.id: row.distance for row in rows}
        records = db.query(DicomStudyRecord).filter(
            DicomStudyRecord.id.in_(distances.keys())
        ).all()
        records.sort(key=lambda record: distances[record.id])
        return [self._record_to_dict(record, {"distance": round(distances[record.id], 4)})
                for record in records]


class FullTextSearchStrategy(SearchStrategy):
    def search(self, db: Session, query: str, limit: int,
               modality: str = None) -> list[dict]:
        sql = text("""
            SELECT id, ts_rank(
                to_tsvector('simple', coalesce(study_description,'') || ' ' || coalesce(image_comments,'')),
                plainto_tsquery('simple', :q)
            ) AS rank
            FROM dicom_studies
            WHERE to_tsvector('simple', coalesce(study_description,'') || ' ' || coalesce(image_comments,''))
                @@ plainto_tsquery('simple', :q)
            {modality_filter}
            ORDER BY rank DESC
            LIMIT :lim
        """.format(modality_filter="AND modality = :mod" if modality else ""))
        params = {"q": query, "lim": limit}
        if modality:
            params["mod"] = modality.upper()
        rows = db.execute(sql, params).fetchall()
        ranks = {row.id: row.rank for row in rows}
        if not ranks:
            return []
        records = db.query(DicomStudyRecord).filter(
            DicomStudyRecord.id.in_(ranks.keys())
        ).all()
        records.sort(key=lambda record: ranks[record.id], reverse=True)
        return [self._record_to_dict(record, {"rank": round(ranks[record.id], 4)})
                for record in records]


class HybridSearchStrategy(SearchStrategy):
    def __init__(self, alpha: float = 0.7):
        self.alpha = alpha

    def search(self, db: Session, query: str, limit: int,
               modality: str = None) -> list[dict]:
        cosine_results = CosineSearchStrategy().search(db, query, limit * 2, modality)
        fulltext_results = FullTextSearchStrategy().search(db, query, limit * 2, modality)

        def normalize(results: list[dict], key: str) -> dict[int, float]:
            if not results:
                return {}
            values = [result[key] for result in results]
            minimum, maximum = min(values), max(values)
            span = maximum - minimum or 1.0
            return {result["id"]: (result[key] - minimum) / span for result in results}

        cosine_scores = normalize(cosine_results, "similarity")
        fulltext_scores = normalize(fulltext_results, "rank")
        combined = {
            record_id: self.alpha * cosine_scores.get(record_id, 0.0)
            + (1 - self.alpha) * fulltext_scores.get(record_id, 0.0)
            for record_id in set(cosine_scores) | set(fulltext_scores)
        }
        top_ids = sorted(combined, key=combined.get, reverse=True)[:limit]
        records = db.query(DicomStudyRecord).filter(
            DicomStudyRecord.id.in_(top_ids)
        ).all()
        records.sort(key=lambda record: combined[record.id], reverse=True)
        return [self._record_to_dict(record, {"score": round(combined[record.id], 4)})
                for record in records]


class SearchStrategyFactory:
    _registry: dict[str, SearchStrategy] = {
        "cosine": CosineSearchStrategy(),
        "euclidean": EuclideanSearchStrategy(),
        "fulltext": FullTextSearchStrategy(),
        "hybrid": HybridSearchStrategy(alpha=0.7),
    }

    @classmethod
    def get(cls, name: str) -> SearchStrategy:
        strategy = cls._registry.get(name)
        if not strategy:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown search strategy '{name}'. Available: {list(cls._registry)}",
            )
        return strategy

    @classmethod
    def available(cls) -> list[str]:
        return list(cls._registry)
