"""Conversation states (persisted in PostgreSQL)."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class BuildingForm(StatesGroup):
    name = State()
    address = State()
    description = State()


class ApartmentForm(StatesGroup):
    building = State()
    number = State()
    floor = State()
    description = State()


class TenantForm(StatesGroup):
    building = State()
    apartment = State()
    name = State()
    phone = State()
    rent = State()
    start_date = State()
    notes = State()
    confirm = State()


class PaymentForm(StatesGroup):
    tenant = State()
    amount = State()
    notes = State()
    confirm = State()


class SearchForm(StatesGroup):
    query = State()


class SettingsForm(StatesGroup):
    currency = State()
    report_time = State()


class EditForm(StatesGroup):
    value = State()


class ContactForm(StatesGroup):
    phone = State()
    note = State()
