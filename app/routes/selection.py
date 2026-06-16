from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session
from app.services.product_service import product_service
from app.services.ali1688_service import ali1688_service
from app.services.erp_category_service import erp_category_service
import threading

selection_bp = Blueprint('selection', __name__)

@selection_bp.route('/selection/<task_id>')
def selection_page(task_id):
    return redirect('/products')

@selection_bp.route('/api/selection/<task_id>/select', methods=['POST'])
def update_selection(task_id):
    return jsonify({'success': True})

@selection_bp.route('/api/selection/<task_id>/confirm', methods=['POST'])
def confirm_selection(task_id):
    return jsonify({'success': True, 'added_count': 0})

@selection_bp.route('/api/selection/<task_id>/export')
def export_selection(task_id):
    return jsonify({'success': True, 'products': []})

@selection_bp.route('/category-sync')
def category_sync_page():
    return render_template('category_sync.html', active_menu='category')