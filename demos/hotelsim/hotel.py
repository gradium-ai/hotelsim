"""Four Seasons Rive Gauche — fictitious hotel data and reservation state.

Reservations live in memory only and are wiped when the process restarts.
"""

from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timezone


HOTEL = {
    "name": "Four Seasons Rive Gauche",
    "address": "32 quai Voltaire, 75007 Paris, France",
    "phone": "+33 1 49 70 00 00",
    "email": "concierge@fs-rivegauche.example",
    "check_in": "15:00",
    "check_out": "12:00",
    "currencies": ["EUR", "USD"],
    "languages": ["Français", "English"],
    "concierge_hours": "24/7",
    "tagline": {
        "fr": "L'élégance parisienne sur les bords de la Seine.",
        "en": "Parisian elegance on the banks of the Seine.",
    },
}


ROOMS: list[dict] = [
    {
        "id": "studio_parisien",
        "name_en": "Studio Parisien",
        "name_fr": "Studio Parisien",
        "size_m2": 28,
        "beds": "1 queen",
        "view_en": "Inner courtyard",
        "view_fr": "Cour intérieure",
        "rate_eur": 795,
        "rate_usd": 850,
        "max_guests": 2,
    },
    {
        "id": "chambre_elegance",
        "name_en": "Élégance Room",
        "name_fr": "Chambre Élégance",
        "size_m2": 35,
        "beds": "1 king",
        "view_en": "Paris rooftops",
        "view_fr": "Toits de Paris",
        "rate_eur": 1120,
        "rate_usd": 1200,
        "max_guests": 2,
    },
    {
        "id": "chambre_deluxe_eiffel",
        "name_en": "Deluxe Eiffel Room",
        "name_fr": "Chambre Deluxe Eiffel",
        "size_m2": 42,
        "beds": "1 king",
        "view_en": "Eiffel Tower",
        "view_fr": "Tour Eiffel",
        "rate_eur": 1540,
        "rate_usd": 1650,
        "max_guests": 2,
    },
    {
        "id": "suite_junior_seine",
        "name_en": "Junior Seine Suite",
        "name_fr": "Suite Junior Seine",
        "size_m2": 65,
        "beds": "1 king + sitting room",
        "view_en": "Seine river",
        "view_fr": "La Seine",
        "rate_eur": 2240,
        "rate_usd": 2400,
        "max_guests": 3,
    },
    {
        "id": "suite_senateur",
        "name_en": "Sénateur Suite",
        "name_fr": "Suite Sénateur",
        "size_m2": 90,
        "beds": "1 king, powder room, private terrace",
        "view_en": "Notre-Dame & Seine",
        "view_fr": "Notre-Dame et la Seine",
        "rate_eur": 3265,
        "rate_usd": 3500,
        "max_guests": 3,
    },
    {
        "id": "suite_presidentielle",
        "name_en": "Présidentielle Suite",
        "name_fr": "Suite Présidentielle",
        "size_m2": 180,
        "beds": "2 bedrooms, dining room, butler service",
        "view_en": "Panoramic, Eiffel Tower & Seine",
        "view_fr": "Panoramique, Tour Eiffel et Seine",
        "rate_eur": 6990,
        "rate_usd": 7500,
        "max_guests": 4,
    },
]


AMENITIES = {
    "fr": [
        "Spa Le Bain — hammam, bain romain, 8 cabines de soin (signature : Rituel Rive Gauche, 90 min, 450 €)",
        "Restaurant L'Académie — étoilé Michelin, cuisine française par la chef Léa Vasseur",
        "Bar Verlaine — rooftop avec vue sur Notre-Dame, cocktails classiques",
        "Piscine intérieure chauffée, sauna et salle de fitness 24h/24",
        "Cinéma privé 8 places (sur réservation, gratuit pour les clients)",
        "Conciergerie 24h/24, voiturier, transferts aéroport en limousine (350 € l'aller)",
        "Animaux acceptés (chiens ≤ 15 kg, supplément 150 € par nuit)",
        "Articles d'accueil Hermès, menu d'oreillers (8 choix), service de majordome dans les suites",
    ],
    "en": [
        "Spa Le Bain — hammam, Roman bath, 8 treatment rooms (signature: Rive Gauche Ritual massage, 90 min, $480)",
        "Restaurant L'Académie — Michelin-starred French cuisine by chef Léa Vasseur",
        "Bar Verlaine — rooftop with Notre-Dame view, classic cocktails",
        "Heated indoor pool, sauna, 24-hour fitness studio",
        "Private 8-seat cinema (complimentary for guests, by reservation)",
        "24/7 concierge, valet parking, limousine airport transfer ($375 each way)",
        "Pet-friendly (dogs ≤ 15 kg, $160 per night)",
        "Hermès amenities, pillow menu (8 options), butler service in all suites",
    ],
}


POLICY = {
    "fr": [
        "Disponibilité : toutes les catégories de chambre sont actuellement disponibles, toutes dates confondues.",
        "Arrivée à 15h00, départ à 12h00 (départ tardif sur demande).",
        "Annulation gratuite jusqu'à 24h avant l'arrivée.",
        "Première nuit débitée à la réservation, à titre de garantie.",
        "Petit-déjeuner continental inclus, brunch en supplément (75 €).",
    ],
    "en": [
        "Availability: every room category is currently open for all dates.",
        "Check-in 3:00 PM, check-out 12:00 noon (late check-out on request).",
        "Free cancellation up to 24 hours before arrival.",
        "First night charged at booking as a deposit.",
        "Continental breakfast included; weekend brunch is $80 extra.",
    ],
}


def get_room(room_id: str) -> dict | None:
    return next((r for r in ROOMS if r["id"] == room_id), None)


def find_room_by_name(name: str) -> dict | None:
    """Loose match by English or French name (case/accents-insensitive-ish)."""
    norm = (name or "").strip().lower()
    if not norm:
        return None
    for r in ROOMS:
        for key in ("id", "name_en", "name_fr"):
            if r[key].lower() == norm:
                return r
    for r in ROOMS:
        for key in ("name_en", "name_fr"):
            if norm in r[key].lower() or r[key].lower() in norm:
                return r
    return None


def _confirmation_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "FSRG-" + "".join(secrets.choice(alphabet) for _ in range(6))


@dataclass
class CallState:
    lang: str = "fr"
    guest_name: str = ""
    guest_email: str = ""
    last_user_turn_idx: int | None = None
    reservations: list[dict] = field(default_factory=list)


def make_reservation(
    state: CallState,
    *,
    guest_name: str,
    guest_email: str,
    room_id: str,
    arrival_date: str,
    nights: int,
    num_guests: int,
    special_requests: str = "",
) -> dict:
    room = get_room(room_id)
    if room is None:
        raise ValueError(f"Unknown room_id: {room_id}")
    nights = max(1, int(nights))
    rate_eur = room["rate_eur"] * nights
    rate_usd = room["rate_usd"] * nights
    code = _confirmation_code()
    reservation = {
        "code": code,
        "guest_name": guest_name,
        "guest_email": guest_email,
        "room_id": room_id,
        "room_name_fr": room["name_fr"],
        "room_name_en": room["name_en"],
        "arrival_date": arrival_date,
        "nights": nights,
        "num_guests": int(num_guests),
        "total_eur": rate_eur,
        "total_usd": rate_usd,
        "special_requests": special_requests,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    state.guest_name = guest_name or state.guest_name
    state.guest_email = guest_email or state.guest_email
    state.reservations.append(reservation)
    return reservation
