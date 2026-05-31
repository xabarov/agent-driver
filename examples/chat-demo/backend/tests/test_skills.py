from __future__ import annotations


async def test_skills_endpoint_lists_curated_skills(client) -> None:
    response = await client.get("/api/skills")

    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["skills"]}
    assert "deep-research-report" in names
    assert payload["uploadEnabled"] is True


async def test_skill_view_endpoint_uses_shared_view_contract(client) -> None:
    response = await client.get("/api/skills/deep-research-report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill"]["name"] == "deep-research-report"
    assert "source-verified report" in payload["content"]
    assert payload["skillInvocation"]["name"] == "deep-research-report"


async def test_skill_upload_indexes_demo_local_skill(client) -> None:
    response = await client.post(
        "/api/skills/uploads",
        json={
            "name": "uploaded-research",
            "content": "---\nname: uploaded-research\n---\n# Uploaded\n",
        },
    )

    assert response.status_code == 200
    assert response.json()["skill"]["name"] == "uploaded-research"
    listing = await client.get("/api/skills")
    names = {item["name"] for item in listing.json()["skills"]}
    assert "uploaded-research" in names
