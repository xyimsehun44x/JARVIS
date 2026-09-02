from __future__ import annotations

from typing import Protocol

from jarvis.agents.email.schemas import ResolvedContact


class ContactProvider(Protocol):
    def search(self, query: str) -> list[ResolvedContact]: ...


class StaticContactProvider:
    def __init__(self, contacts: list[ResolvedContact] | None = None) -> None:
        self.contacts = contacts if contacts is not None else [
            ResolvedContact(name="David Kim", email="david.kim@example.com"),
            ResolvedContact(name="David Lee", email="david.lee@example.com"),
            ResolvedContact(name="Jisoo Park", email="jisoo@example.com"),
        ]

    def search(self, query: str) -> list[ResolvedContact]:
        needle = query.strip().lower()
        if "@" in needle:
            exact = [contact for contact in self.contacts if contact.email.lower() == needle]
            return exact or [ResolvedContact(name=needle.split("@")[0], email=needle)]
        return [contact for contact in self.contacts if needle in contact.name.lower()]


class GooglePeopleProvider:
    def __init__(self, oauth) -> None:
        self.oauth = oauth

    def search(self, query: str) -> list[ResolvedContact]:
        needle = query.strip().lower()
        matches: dict[str, ResolvedContact] = {}
        page_token: str | None = None
        while True:
            response = (
                self.oauth.service("people", "v1")
                .people()
                .connections()
                .list(
                    resourceName="people/me",
                    personFields="names,emailAddresses",
                    pageSize=1000,
                    pageToken=page_token,
                )
                .execute()
            )
            for person in response.get("connections", []):
                names = person.get("names", [])
                emails = person.get("emailAddresses", [])
                if not names or not emails:
                    continue
                name = names[0].get("displayName", "").strip()
                for email_entry in emails:
                    email = email_entry.get("value", "").strip()
                    if not name or not email:
                        continue
                    if needle in name.lower() or needle == email.lower():
                        matches[email.lower()] = ResolvedContact(name=name, email=email)
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return sorted(matches.values(), key=lambda contact: (contact.name, contact.email))
