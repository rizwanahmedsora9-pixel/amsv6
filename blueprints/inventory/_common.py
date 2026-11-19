"""Shared imports and module globals."""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import login_required, current_user
from datetime import date, datetime
from types import SimpleNamespace
from sqlalchemy import func, case, or_, and_
from models import db, Material, Service, MaterialCategory, Entry, Client, PendingBill, DirectSale, Booking, Payment, GRN, Invoice, StockAdjustment
from app.services.lookups import is_service_name

# Module configuration
MODULE_CONFIG = {
    'name': 'Inventory Module',
    'description': 'Stock and inventory management',
    'url_prefix': '/inventory',
    'enabled': True
}

inventory_bp = Blueprint('inventory', __name__)



