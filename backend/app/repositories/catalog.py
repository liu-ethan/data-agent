"""MySQL authority for collected schema, semantic catalog and index manifests."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import uuid4

from sqlalchemy import text

from ..errors import RuntimeAgentError
from ..models import CatalogField, CatalogObject, JoinPath, PermissionContext
from ..services.schema_catalog import (
    CatalogSnapshot, IndexManifest, SearchDocument, content_version, lexical_tokens,
    stable_id, token_frequencies,
)
from .runtime import RuntimePersistence


def _params(prefix: str, values: Iterable[str]) -> tuple[str, dict[str, str]]:
    items = list(values)
    return ",".join(f":{prefix}_{index}" for index in range(len(items))), {
        f"{prefix}_{index}": value for index, value in enumerate(items)}


class MySQLCatalogRepository:
    """Authoritative catalog repository; Milvus stores document pointers only."""

    def __init__(self, persistence: RuntimePersistence) -> None:
        self.persistence = persistence

    @property
    def engine(self):
        return self.persistence.engine

    def synchronize(self, snapshot: CatalogSnapshot) -> CatalogSnapshot:
        """Atomically replace one source's physical snapshot and lexical docs."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.engine.begin() as connection:
            metrics = [dict(row) for row in connection.execute(text(
                "SELECT metric_id,name,formula,time_field,grain_json,required_filters_json,"
                "forbidden_join_patterns_json,null_policy,rounding,metric_version "
                "FROM metric_definitions ORDER BY metric_id")).mappings().all()]
            aliases = [dict(row) for row in connection.execute(text(
                "SELECT alias_id,alias_text,target_type,target_id,version "
                "FROM entity_aliases ORDER BY alias_id")).mappings().all()]
            curated_relations = [dict(row) for row in connection.execute(text(
                "SELECT relation_id,left_ref,right_ref,cardinality,verified,relation_version "
                "FROM table_relations WHERE verified=TRUE ORDER BY relation_id"
            )).mappings().all()]
            # IDs with this reserved prefix are collector-owned physical FKs;
            # they are already represented by snapshot.relations below.
            curated_relations = [row for row in curated_relations
                                 if not str(row["relation_id"]).startswith("relation_")]
            authority_payload = snapshot.canonical_payload() | {
                "metrics": metrics, "aliases": aliases,
                "curated_relations": curated_relations,
            }
            version = content_version(authority_payload)
            snapshot = snapshot.with_version(version)

            connection.execute(text("""
                INSERT INTO catalog_sources
                  (source_id,name,domain,catalog_version,owner,created_at)
                VALUES (:source_id,:name,:domain,:version,:owner,:created_at)
                ON DUPLICATE KEY UPDATE name=VALUES(name),domain=VALUES(domain),
                  catalog_version=VALUES(catalog_version),owner=VALUES(owner)
            """), {"source_id": snapshot.source_id, "name": snapshot.source_name,
                    "domain": snapshot.domain, "version": version,
                    "owner": "schema-collector", "created_at": now})

            old_objects = connection.execute(text(
                "SELECT object_id,object_name FROM catalog_objects "
                "WHERE source_id=:source_id"), {"source_id": snapshot.source_id}
            ).mappings().all()
            object_ids = {str(row["object_name"]): str(row["object_id"])
                          for row in old_objects}
            selected_object_ids: dict[str, str] = {}
            for item in snapshot.objects:
                object_id = object_ids.get(item.name) or stable_id(
                    "obj", snapshot.source_id, item.name)
                selected_object_ids[item.name] = object_id
                connection.execute(text("""
                    INSERT INTO catalog_objects
                      (object_id,source_id,object_name,grain,object_type,catalog_version)
                    VALUES (:object_id,:source_id,:name,:grain,:type,:version)
                    ON DUPLICATE KEY UPDATE source_id=VALUES(source_id),
                      object_name=VALUES(object_name),grain=VALUES(grain),
                      object_type=VALUES(object_type),catalog_version=VALUES(catalog_version)
                """), {"object_id": object_id, "source_id": snapshot.source_id,
                        "name": item.name, "grain": item.grain,
                        "type": item.object_type, "version": version})
                connection.execute(text("""
                    INSERT INTO catalog_object_metadata(object_id,description)
                    VALUES (:object_id,:description)
                    ON DUPLICATE KEY UPDATE description=VALUES(description)
                """), {"object_id": object_id, "description": item.comment})

            stale_objects = set(object_ids.values()) - set(selected_object_ids.values())
            if stale_objects:
                clause, values = _params("stale_object", stale_objects)
                connection.execute(text(
                    f"DELETE FROM catalog_fields WHERE object_id IN ({clause})"), values)
                connection.execute(text(
                    f"DELETE FROM catalog_objects WHERE object_id IN ({clause})"), values)

            selected_field_ids: dict[tuple[str, str], str] = {}
            for table_name, object_id in selected_object_ids.items():
                existing = connection.execute(text(
                    "SELECT field_id,field_name FROM catalog_fields WHERE object_id=:object_id"),
                    {"object_id": object_id}).mappings().all()
                existing_ids = {str(row["field_name"]): str(row["field_id"])
                                for row in existing}
                current_names: set[str] = set()
                for item in (field for field in snapshot.fields
                             if field.table_name == table_name):
                    current_names.add(item.field_name)
                    field_id = existing_ids.get(item.field_name) or stable_id(
                        "field", snapshot.source_id, table_name, item.field_name)
                    selected_field_ids[(table_name, item.field_name)] = field_id
                    connection.execute(text("""
                        INSERT INTO catalog_fields
                          (field_id,object_id,field_name,data_type,nullable,
                           classification,is_time_field,catalog_version)
                        VALUES (:field_id,:object_id,:name,:data_type,:nullable,
                                :classification,:is_time,:version)
                        ON DUPLICATE KEY UPDATE object_id=VALUES(object_id),
                          field_name=VALUES(field_name),data_type=VALUES(data_type),
                          nullable=VALUES(nullable),classification=VALUES(classification),
                          is_time_field=VALUES(is_time_field),catalog_version=VALUES(catalog_version)
                    """), {"field_id": field_id, "object_id": object_id,
                            "name": item.field_name, "data_type": item.data_type,
                            "nullable": item.nullable,
                            "classification": item.classification,
                            "is_time": item.is_time_field, "version": version})
                    connection.execute(text("""
                        INSERT INTO catalog_field_metadata
                          (field_id,ordinal_position,column_type,description)
                        VALUES (:field_id,:ordinal,:column_type,:description)
                        ON DUPLICATE KEY UPDATE ordinal_position=VALUES(ordinal_position),
                          column_type=VALUES(column_type),description=VALUES(description)
                    """), {"field_id": field_id, "ordinal": item.ordinal,
                            "column_type": item.column_type,
                            "description": item.comment})
                stale_fields = set(existing_ids) - current_names
                if stale_fields:
                    ids = [existing_ids[name] for name in stale_fields]
                    clause, values = _params("stale_field", ids)
                    connection.execute(text(
                        f"DELETE FROM catalog_fields WHERE field_id IN ({clause})"), values)

            old_physical = connection.execute(text("""
                SELECT relation_id FROM catalog_relation_sources
                WHERE source_id=:source_id AND origin='PHYSICAL'
            """), {"source_id": snapshot.source_id}).scalars().all()
            if old_physical:
                physical_clause, physical_params = _params("physical", old_physical)
                connection.execute(text(
                    "DELETE FROM catalog_relation_sources WHERE source_id=:source_id "
                    "AND origin='PHYSICAL'"), {"source_id": snapshot.source_id})
                connection.execute(text(
                    f"DELETE FROM table_relations WHERE relation_id IN ({physical_clause})"),
                    physical_params)
            for item in snapshot.relations:
                relation_id = stable_id(
                    "relation", snapshot.source_id, item.left_table, item.left_field,
                    item.right_table, item.right_field)
                connection.execute(text("""
                    INSERT INTO table_relations
                      (relation_id,left_ref,right_ref,cardinality,verified,relation_version)
                    VALUES (:id,:left_ref,:right_ref,:cardinality,TRUE,:version)
                    ON DUPLICATE KEY UPDATE left_ref=VALUES(left_ref),
                      right_ref=VALUES(right_ref),cardinality=VALUES(cardinality),
                      verified=TRUE,relation_version=VALUES(relation_version)
                """), {"id": relation_id,
                        "left_ref": f"{item.left_table}.{item.left_field}",
                        "right_ref": f"{item.right_table}.{item.right_field}",
                        "cardinality": item.cardinality, "version": version})
                connection.execute(text("""
                    INSERT INTO catalog_relation_sources(relation_id,source_id,origin)
                    VALUES (:relation_id,:source_id,'PHYSICAL')
                """), {"relation_id": relation_id, "source_id": snapshot.source_id})

            connection.execute(text(
                "DELETE FROM catalog_metric_sources WHERE source_id=:source_id"),
                {"source_id": snapshot.source_id})
            connection.execute(text(
                "DELETE FROM catalog_relation_sources WHERE source_id=:source_id "
                "AND origin='CURATED'"), {"source_id": snapshot.source_id})
            table_names = set(selected_object_ids)
            for relation in curated_relations:
                left_table = str(relation["left_ref"]).split(".")[0]
                right_table = str(relation["right_ref"]).split(".")[0]
                if left_table in table_names and right_table in table_names:
                    connection.execute(text("""
                        INSERT INTO catalog_relation_sources(relation_id,source_id,origin)
                        VALUES (:relation_id,:source_id,'CURATED')
                        ON DUPLICATE KEY UPDATE origin=VALUES(origin)
                    """), {"relation_id": relation["relation_id"],
                            "source_id": snapshot.source_id})
            for metric in metrics:
                references = self._references(" ".join(
                    str(metric.get(key) or "") for key in
                    ("formula", "time_field", "required_filters_json")))
                if references & table_names:
                    connection.execute(text("""
                        INSERT INTO catalog_metric_sources(metric_id,source_id)
                        VALUES (:metric_id,:source_id)
                    """), {"metric_id": metric["metric_id"],
                            "source_id": snapshot.source_id})

            connection.execute(text("""
                INSERT INTO catalog_snapshots
                  (source_id,catalog_version,database_name,snapshot_json,created_at)
                VALUES (:source_id,:version,:database,:payload,:created_at)
                ON DUPLICATE KEY UPDATE snapshot_json=VALUES(snapshot_json)
            """), {"source_id": snapshot.source_id, "version": version,
                    "database": snapshot.database,
                    "payload": json.dumps(snapshot.canonical_payload(), ensure_ascii=False),
                    "created_at": now})
            physical_relations = [{
                "relation_id": stable_id(
                    "relation", snapshot.source_id, item.left_table, item.left_field,
                    item.right_table, item.right_field),
                "left_ref": f"{item.left_table}.{item.left_field}",
                "right_ref": f"{item.right_table}.{item.right_field}",
                "cardinality": item.cardinality,
            } for item in snapshot.relations]
            all_relations = list({str(item["relation_id"]): item for item in
                                  [*curated_relations, *physical_relations]}.values())
            documents = self._documents(
                snapshot, selected_object_ids, selected_field_ids,
                metrics, aliases, all_relations)
            self._replace_documents(connection, snapshot.source_id, documents)
        return snapshot

    def version(self, source_ids: list[str]) -> str:
        if not source_ids:
            raise RuntimeAgentError("CATALOG_VERSION_MISMATCH", "no authorized source")
        clause, values = _params("source", source_ids)
        with self.engine.connect() as connection:
            rows = connection.execute(text(
                f"SELECT source_id,catalog_version FROM catalog_sources "
                f"WHERE source_id IN ({clause})"), values).mappings().all()
        if len(rows) != len(set(source_ids)):
            raise RuntimeAgentError("CATALOG_VERSION_MISMATCH",
                                    "an authorized catalog source is missing")
        versions = {str(row["catalog_version"]) for row in rows}
        if len(versions) != 1:
            raise RuntimeAgentError("CATALOG_VERSION_MISMATCH",
                                    "catalog sources have incompatible versions")
        return versions.pop()

    def documents(self, version: str) -> list[SearchDocument]:
        with self.engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT d.document_id,d.layer,d.document_kind,d.target_id,d.source_id,
                       d.object_id,d.catalog_version,d.content,d.document_length,
                       COALESCE(f.classification,'BUSINESS') AS classification
                FROM catalog_search_documents d
                LEFT JOIN catalog_fields f ON d.layer='field_entity'
                  AND f.field_id=d.target_id
                WHERE d.catalog_version=:version
                ORDER BY d.layer,d.document_id
            """), {"version": version}).mappings().all()
        return [SearchDocument(
            document_id=str(row["document_id"]), layer=str(row["layer"]),
            kind=str(row["document_kind"]), target_id=str(row["target_id"]),
            source_id=str(row["source_id"]), object_id=str(row["object_id"]),
            catalog_version=str(row["catalog_version"]), text=str(row["content"]),
            token_count=int(row["document_length"]),
            classification=str(row["classification"]),
        ) for row in rows]

    def lexical_search(self, query: str, permission: PermissionContext, limit: int,
                       *, layers: tuple[str, ...] = ("object",),
                       object_ids: list[str] | None = None) -> list[dict[str, Any]]:
        if not permission.allowed_source_ids:
            return []
        version = self.version(permission.allowed_source_ids)
        terms = list(dict.fromkeys(lexical_tokens(query)))[:32]
        if not terms:
            return []
        source_clause, params = _params("source", permission.allowed_source_ids)
        term_clause, term_params = _params("term", terms)
        layer_clause, layer_params = _params("layer", layers)
        params |= term_params | layer_params | {"version": version}
        denied_filter = ""
        if permission.denied_classifications:
            denied_clause, denied_params = _params(
                "denied", permission.denied_classifications)
            params |= denied_params
            denied_filter = (
                " AND (d.layer<>'field_entity' OR d.target_id IN "
                f"(SELECT field_id FROM catalog_fields WHERE classification NOT IN ({denied_clause})))")
        object_filter = ""
        if object_ids:
            object_clause, object_params = _params("object", object_ids)
            params |= object_params
            object_filter = f" AND d.object_id IN ({object_clause})"
        scope = (f"d.source_id IN ({source_clause}) AND d.catalog_version=:version "
                 f"AND d.layer IN ({layer_clause}){object_filter}{denied_filter}")
        with self.engine.connect() as connection:
            corpus = connection.execute(text(
                f"SELECT COUNT(*) AS n,COALESCE(AVG(document_length),1) AS avgdl "
                f"FROM catalog_search_documents d WHERE {scope}"), params).mappings().one()
            dfs = connection.execute(text(
                f"SELECT t.term,COUNT(*) AS df FROM catalog_search_terms t "
                f"JOIN catalog_search_documents d ON d.document_id=t.document_id "
                f"WHERE {scope} AND t.term IN ({term_clause}) GROUP BY t.term"),
                params).mappings().all()
            matches = connection.execute(text(
                f"SELECT d.document_id,d.layer,d.document_kind,d.target_id,d.source_id,"
                f"d.object_id,d.document_length,t.term,t.term_frequency "
                f"FROM catalog_search_terms t JOIN catalog_search_documents d "
                f"ON d.document_id=t.document_id WHERE {scope} "
                f"AND t.term IN ({term_clause})"), params).mappings().all()
        return self._bm25(matches, int(corpus["n"] or 0),
                          float(corpus["avgdl"] or 1),
                          {str(row["term"]): int(row["df"]) for row in dfs}, limit)

    def hydrate(self, ids: list[str], permission: PermissionContext, *,
                field_ids: list[str] | None = None) -> tuple[
                    list[CatalogObject], list[CatalogField], list[JoinPath], str]:
        if not ids:
            return [], [], [], self.version(permission.allowed_source_ids)
        version = self.version(permission.allowed_source_ids)
        id_clause, params = _params("id", ids)
        source_clause, source_params = _params("source", permission.allowed_source_ids)
        params |= source_params | {"version": version}
        with self.engine.connect() as connection:
            rows = connection.execute(text(f"""
                SELECT o.object_id,o.object_name,o.grain,o.source_id,s.domain
                FROM catalog_objects o JOIN catalog_sources s ON s.source_id=o.source_id
                WHERE o.object_id IN ({id_clause}) AND o.source_id IN ({source_clause})
                  AND o.catalog_version=:version
            """), params).mappings().all()
            object_ids = [str(row["object_id"]) for row in rows]
            object_clause, object_params = _params("field_object", object_ids)
            fields = connection.execute(text(f"""
                SELECT f.field_id,f.object_id,f.field_name,f.data_type,f.nullable,
                       f.classification,f.is_time_field,COALESCE(m.ordinal_position,9999) ordinal
                FROM catalog_fields f LEFT JOIN catalog_field_metadata m
                  ON m.field_id=f.field_id
                WHERE f.object_id IN ({object_clause}) AND f.catalog_version=:version
                ORDER BY f.object_id,ordinal,f.field_name
            """) if object_ids else text(
                "SELECT field_id,object_id,field_name,data_type,nullable,classification,"
                "is_time_field,0 ordinal FROM catalog_fields WHERE 1=0"),
                object_params | {"version": version}).mappings().all()
            relations = connection.execute(text(f"""
                SELECT DISTINCT r.relation_id,r.left_ref,r.right_ref,
                       r.cardinality,r.verified
                FROM table_relations r JOIN catalog_relation_sources s
                  ON s.relation_id=r.relation_id
                WHERE r.verified=TRUE AND s.source_id IN ({source_clause})
            """), source_params).mappings().all()
            aliases = connection.execute(text("""
                SELECT alias_text,target_id FROM entity_aliases
                WHERE UPPER(target_type)='DIMENSION'
            """)).mappings().all()
        order = {object_id: index for index, object_id in enumerate(ids)}
        objects = [CatalogObject(
            object_id=str(row["object_id"]), name=str(row["object_name"]),
            grain=str(row["grain"]), source_id=str(row["source_id"]),
            domain=str(row["domain"]), score=0,
            retrieval_method="mysql-bm25+milvus-dense+llm-reranker",
            index_version=version, permission_policy_version=permission.policy_version,
        ) for row in rows]
        objects.sort(key=lambda item: order.get(item.object_id, len(order)))
        names = {item.object_id: item.name for item in objects}
        denied = set(permission.denied_classifications)
        field_scores = {field_id: 1 - index / max(1, len(field_ids or []))
                        for index, field_id in enumerate(field_ids or [])}
        aliases_by_target: dict[str, list[str]] = defaultdict(list)
        for row in aliases:
            aliases_by_target[str(row["target_id"])].append(str(row["alias_text"]))
        output_fields = [CatalogField(
            field_id=str(row["field_id"]),
            name=f"{names[str(row['object_id'])]}.{row['field_name']}",
            data_type=str(row["data_type"]), nullable=bool(row["nullable"]),
            classification=str(row["classification"]),
            aliases=aliases_by_target.get(str(row["field_id"]), [])
            + aliases_by_target.get(
                f"{names[str(row['object_id'])]}.{row['field_name']}", []),
            score=max(0, min(1, field_scores.get(str(row["field_id"]), 0.5))),
            object_id=str(row["object_id"]),
            retrieval_method="mysql-bm25+milvus-dense",
            index_version=version, permission_policy_version=permission.policy_version,
        ) for row in fields if str(row["classification"]) not in denied]
        object_names = {item.name for item in objects}
        joins: list[JoinPath] = []
        seen_joins: set[tuple[str, str]] = set()
        for row in relations:
            left, right = str(row["left_ref"]), str(row["right_ref"])
            if (left.split(".")[0] not in object_names
                    or right.split(".")[0] not in object_names):
                continue
            key = tuple(sorted((left, right)))
            if key in seen_joins:
                continue
            seen_joins.add(key)
            joins.append(JoinPath(
                join_id=str(row["relation_id"]), left=left, right=right,
                cardinality=str(row["cardinality"]), verified=bool(row["verified"])))
        return objects, output_fields, joins, version

    def expand_object_ids(self, seed_ids: list[str], permission: PermissionContext,
                          max_hops: int, max_objects: int,
                          required_names: set[str] | None = None) -> list[str]:
        version = self.version(permission.allowed_source_ids)
        source_clause, params = _params("source", permission.allowed_source_ids)
        with self.engine.connect() as connection:
            objects = connection.execute(text(f"""
                SELECT object_id,object_name FROM catalog_objects
                WHERE source_id IN ({source_clause}) AND catalog_version=:version
            """), params | {"version": version}).mappings().all()
            relations = connection.execute(text(f"""
                SELECT DISTINCT r.left_ref,r.right_ref
                FROM table_relations r JOIN catalog_relation_sources s
                  ON s.relation_id=r.relation_id
                WHERE r.verified=TRUE AND s.source_id IN ({source_clause})
            """), params).mappings().all()
        by_name = {str(row["object_name"]): str(row["object_id"]) for row in objects}
        graph: dict[str, set[str]] = defaultdict(set)
        for row in relations:
            left, right = str(row["left_ref"]).split(".")[0], str(row["right_ref"]).split(".")[0]
            if left in by_name and right in by_name:
                graph[left].add(right); graph[right].add(left)
        by_id = {object_id: name for name, object_id in by_name.items()}
        required = [by_name[name] for name in sorted(required_names or set())
                    if name in by_name]
        desired = list(dict.fromkeys([*required, *seed_ids]))[:max_objects]
        desired_names = [by_id[item] for item in desired if item in by_id]
        selected = list(desired)

        def shortest_path(start: str, end: str) -> list[str]:
            queue = deque([(start, [start])]); visited = {start}
            while queue:
                current, path = queue.popleft()
                if len(path) - 1 >= max_hops:
                    continue
                for neighbor in sorted(graph[current]):
                    if neighbor == end:
                        return [*path, neighbor]
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, [*path, neighbor]))
            return []

        for index, left in enumerate(desired_names):
            for right in desired_names[index + 1:]:
                for name in shortest_path(left, right)[1:-1]:
                    object_id = by_name[name]
                    if object_id not in selected and len(selected) < max_objects:
                        selected.append(object_id)
        # Required semantic dependencies always outrank vector-only candidates.
        return list(dict.fromkeys([*required, *selected]))[:max_objects]

    def semantic_bindings(self, query: str, permission: PermissionContext,
                          dense_metric_ids: list[str] | None = None) -> tuple[
                              list[str], list[str], set[str], set[str]]:
        source_clause, params = _params("source", permission.allowed_source_ids)
        with self.engine.connect() as connection:
            metrics = connection.execute(text(f"""
                SELECT m.metric_id,m.name,m.formula,m.time_field,m.required_filters_json
                FROM metric_definitions m JOIN catalog_metric_sources s
                  ON s.metric_id=m.metric_id
                WHERE s.source_id IN ({source_clause})
            """), params).mappings().all()
            aliases = connection.execute(text(
                "SELECT alias_text,target_type,target_id FROM entity_aliases"
            )).mappings().all()
            authorized_fields = connection.execute(text(f"""
                SELECT f.field_id,CONCAT(o.object_name,'.',f.field_name) qualified_name
                FROM catalog_fields f JOIN catalog_objects o ON o.object_id=f.object_id
                WHERE o.source_id IN ({source_clause})
            """), params).mappings().all()
        lowered = query.lower()
        available = {str(row["metric_id"]): row for row in metrics}
        allowed_dimension_refs = {
            str(value) for row in authorized_fields
            for value in (row["field_id"], row["qualified_name"])}
        metric_ids = [metric_id for metric_id, row in available.items()
                      if metric_id.lower() in lowered or str(row["name"]).lower() in lowered]
        if not metric_ids:
            metric_ids.extend(item for item in (dense_metric_ids or [])[:1]
                              if item in available)
        dimensions: list[str] = []
        for row in aliases:
            if str(row["alias_text"]).lower() not in lowered:
                continue
            kind, target = str(row["target_type"]).upper(), str(row["target_id"])
            if kind == "METRIC" and target in available:
                metric_ids.append(target)
            elif kind == "DIMENSION" and target in allowed_dimension_refs:
                dimensions.append(target)
        metric_ids = list(dict.fromkeys(metric_ids))
        required_tables: set[str] = set()
        required_fields: set[str] = set(dimensions)
        required_tables.update(
            item.split(".", 1)[0] for item in dimensions if "." in item)
        for metric_id in metric_ids:
            row = available[metric_id]
            refs = self._qualified_references(" ".join(
                str(row[key] or "") for key in
                ("formula", "time_field", "required_filters_json")))
            required_fields.update(refs)
            required_tables.update(ref.split(".")[0] for ref in refs)
        return metric_ids, list(dict.fromkeys(dimensions)), required_tables, required_fields

    def active_manifest(self, catalog_version: str | None = None) -> IndexManifest:
        where, params = "status='ACTIVE'", {}
        if catalog_version:
            where += " AND catalog_version=:version"; params["version"] = catalog_version
        with self.engine.connect() as connection:
            row = connection.execute(text(f"""
                SELECT manifest_id,catalog_version,index_version,embedding_provider,
                       embedding_model,embedding_dimension,collections_json,
                       document_counts_json
                FROM catalog_index_manifests WHERE {where}
                ORDER BY activated_at DESC LIMIT 1
            """), params).mappings().first()
        if not row:
            raise RuntimeAgentError("RAG_INDEX_MISSING",
                                    "no active Milvus index manifest exists")
        return IndexManifest(
            manifest_id=str(row["manifest_id"]),
            catalog_version=str(row["catalog_version"]),
            index_version=str(row["index_version"]),
            embedding_provider=str(row["embedding_provider"]),
            embedding_model=str(row["embedding_model"]),
            embedding_dimension=int(row["embedding_dimension"]),
            collections=json.loads(row["collections_json"]),
            document_counts=json.loads(row["document_counts_json"]),
        )

    def activate_manifest(self, *, catalog_version: str, index_version: str,
                          embedding_provider: str, embedding_model: str,
                          embedding_dimension: int, collections: dict[str, str],
                          document_counts: dict[str, int]) -> IndexManifest:
        manifest_id = f"manifest_{uuid4().hex[:20]}"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.engine.begin() as connection:
            connection.execute(text(
                "UPDATE catalog_index_manifests SET status='RETIRED' "
                "WHERE status='ACTIVE'"))
            connection.execute(text("""
                INSERT INTO catalog_index_manifests
                  (manifest_id,catalog_version,index_version,embedding_provider,
                   embedding_model,embedding_dimension,collections_json,
                   document_counts_json,status,created_at,activated_at)
                VALUES (:manifest_id,:catalog_version,:index_version,:provider,
                        :model,:dimension,:collections,:counts,'ACTIVE',:now,:now)
                ON DUPLICATE KEY UPDATE embedding_provider=VALUES(embedding_provider),
                  embedding_model=VALUES(embedding_model),
                  embedding_dimension=VALUES(embedding_dimension),
                  collections_json=VALUES(collections_json),
                  document_counts_json=VALUES(document_counts_json),
                  status='ACTIVE',activated_at=VALUES(activated_at)
            """), {"manifest_id": manifest_id, "catalog_version": catalog_version,
                    "index_version": index_version, "provider": embedding_provider,
                    "model": embedding_model, "dimension": embedding_dimension,
                    "collections": json.dumps(collections, sort_keys=True),
                    "counts": json.dumps(document_counts, sort_keys=True), "now": now})
        return self.active_manifest(catalog_version)

    @staticmethod
    def _references(value: str) -> set[str]:
        return {item.split(".")[0] for item in
                re.findall(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value)}

    @staticmethod
    def _qualified_references(value: str) -> set[str]:
        return set(re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value))

    @staticmethod
    def _bm25(rows: list[Any], n: int, avgdl: float, dfs: dict[str, int],
              limit: int, k1: float = 1.5, b: float = 0.75) -> list[dict[str, Any]]:
        if not rows or n <= 0:
            return []
        scores: dict[str, float] = defaultdict(float)
        metadata: dict[str, dict[str, Any]] = {}
        for row in rows:
            document_id, term = str(row["document_id"]), str(row["term"])
            tf, dl, df = int(row["term_frequency"]), int(row["document_length"]), dfs[term]
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            scores[document_id] += idf * (
                tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / max(avgdl, 1))))
            metadata[document_id] = {
                "target_id": str(row["target_id"]), "layer": str(row["layer"]),
                "kind": str(row["document_kind"]), "source_id": str(row["source_id"]),
                "object_id": str(row["object_id"]),
            }
        ranked = sorted(scores, key=lambda item: (-scores[item], item))[:limit]
        maximum = max((scores[item] for item in ranked), default=1)
        return [metadata[item] | {"score": round(scores[item] / maximum, 6)}
                for item in ranked]

    def _documents(self, snapshot: CatalogSnapshot,
                   object_ids: dict[str, str], field_ids: dict[tuple[str, str], str],
                   metrics: list[dict[str, Any]], aliases: list[dict[str, Any]],
                   relations: list[dict[str, Any]]) -> list[SearchDocument]:
        aliases_by_target: dict[str, list[str]] = defaultdict(list)
        for row in aliases:
            aliases_by_target[str(row["target_id"])].append(str(row["alias_text"]))
        documents: list[SearchDocument] = []

        def add(layer: str, kind: str, target_id: str, value: str,
                object_id: str = "", classification: str = "BUSINESS") -> None:
            tokens = lexical_tokens(value)
            documents.append(SearchDocument(
                document_id=stable_id("doc", snapshot.source_id, layer, kind, target_id),
                layer=layer, kind=kind, target_id=target_id,
                source_id=snapshot.source_id, object_id=object_id,
                catalog_version=snapshot.catalog_version, text=value,
                token_count=max(1, len(tokens)), classification=classification))

        add("source_domain", "source", snapshot.source_id,
            f"source {snapshot.source_name}; domain {snapshot.domain}; database {snapshot.database}")
        fields_by_table: dict[str, list[Any]] = defaultdict(list)
        for item in snapshot.fields:
            fields_by_table[item.table_name].append(item)
        for item in snapshot.objects:
            object_id = object_ids[item.name]
            field_text = " | ".join(
                f"{field.field_name} {field.data_type} {field.classification} {field.comment}"
                for field in fields_by_table[item.name]
                if field.classification not in {"PHONE", "ID_CARD"})
            add("object", "object", object_id,
                f"table {item.name}; grain {item.grain}; domain {snapshot.domain}; "
                f"description {item.comment}; fields {field_text}; aliases "
                f"{' '.join(aliases_by_target[object_id])}", object_id)
        for item in snapshot.fields:
            object_id = object_ids[item.table_name]
            field_id = field_ids[(item.table_name, item.field_name)]
            qualified = f"{item.table_name}.{item.field_name}"
            add("field_entity", "field", field_id,
                f"field {qualified}; type {item.column_type}; classification "
                f"{item.classification}; description {item.comment}; aliases "
                f"{' '.join(aliases_by_target[field_id] + aliases_by_target[qualified])}",
                object_id, item.classification)
        table_names = set(object_ids)
        for metric in metrics:
            refs = self._references(" ".join(str(metric.get(key) or "") for key in
                                    ("formula", "time_field", "required_filters_json")))
            if not refs & table_names:
                continue
            metric_id = str(metric["metric_id"])
            add("object", "metric", metric_id,
                f"metric {metric_id}; name {metric['name']}; formula {metric['formula']}; "
                f"time field {metric['time_field']}; required filters "
                f"{metric['required_filters_json']}; aliases "
                f"{' '.join(aliases_by_target[metric_id])}")
        for row in relations:
            left, right = str(row["left_ref"]), str(row["right_ref"])
            if left.split(".")[0] in table_names and right.split(".")[0] in table_names:
                add("relation", "relation", str(row["relation_id"]),
                    f"verified join {left} to {right}; cardinality {row['cardinality']}")
        return documents

    @staticmethod
    def _replace_documents(connection: Any, source_id: str,
                           documents: list[SearchDocument]) -> None:
        connection.execute(text(
            "DELETE FROM catalog_search_documents WHERE source_id=:source_id"),
            {"source_id": source_id})
        for item in documents:
            connection.execute(text("""
                INSERT INTO catalog_search_documents
                  (document_id,layer,document_kind,target_id,source_id,object_id,
                   catalog_version,content,document_length)
                VALUES (:document_id,:layer,:kind,:target_id,:source_id,:object_id,
                        :version,:content,:length)
            """), {"document_id": item.document_id, "layer": item.layer,
                    "kind": item.kind, "target_id": item.target_id,
                    "source_id": item.source_id, "object_id": item.object_id,
                    "version": item.catalog_version, "content": item.text,
                    "length": item.token_count})
            frequencies = token_frequencies(lexical_tokens(item.text))
            term_rows = [{"document_id": item.document_id, "term": term,
                          "frequency": frequency}
                         for term, frequency in frequencies.items()]
            if term_rows:
                connection.execute(text("""
                    INSERT INTO catalog_search_terms(document_id,term,term_frequency)
                    VALUES (:document_id,:term,:frequency)
                """), term_rows)
