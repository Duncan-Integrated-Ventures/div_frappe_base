import frappe

from frappe.query_builder import DocType
from frappe.query_builder.functions import Sum


def get_total_stock_quantity(item_code, inventory_dimensions=None):
	"""
	Fetch the total quantity in stock for an item across all warehouses.

	Args:
	        item_code (str): The item code to query
	        inventory_dimensions: (Dict[str], optional): Inventory Dimensions and their values to filter on {"Customer": "Spacex", "Feeder": "F382"}

	Returns:
	        float: Total actual quantity in stock across all warehouses

	Example:
	        # Get total stock for an item
	        total_qty = get_total_stock_quantity("ITEM-001")

	        # Get stock for a specific batch
	        batch_qty = get_total_stock_quantity("ITEM-001", ["Batch"], {"Batch": "BATCH-123"})
	"""

	# Get the Stock Ledger Entry DocType
	sle = DocType("Stock Ledger Entry")

	# Build the base query
	query = (
		frappe.qb.from_(sle)
		.select(Sum(sle.actual_qty).as_("total_qty"))
		.where(sle.item_code == item_code)
		.where(sle.is_cancelled == 0)
	)

	# Add inventory dimension filter if provided
	if inventory_dimensions:
		for dim, val in inventory_dimensions.items():
			query = query.where(sle[dim] == val)

	# Execute the query
	result = query.run(as_dict=True)

	# Return the total quantity (default to 0 if None)
	return result[0].get("total_qty") or 0.0


@frappe.whitelist()
def get_child_list(doctype, parent, fields=None, pluck=None):
	return frappe.get_all(doctype, filters={"parent": parent}, fields=None, pluck=pluck)


def group_insert(docs, commit_each=False):
	for d in docs:
		d.insert()
		if commit_each:
			frappe.db.commit()
	if not commit_each:
		frappe.db.commit()


def get_item_variant(item_name=None, item=None):
	if item_name:
		item = frappe.get_doc("Item", item_name)
	template = frappe.get_doc("Item", item.variant_of)
	item._attributes = {}
	for value, temp in zip(item.attributes, template.attributes):
		v = value.attribute_value
		if temp.numeric_values:
			if v.isdigit():
				v = int(v)
			else:
				v = float(v)
		item._attributes[value.attribute] = v

	return item
