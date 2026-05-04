# Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
# For license information, please see license.txt

app_name = "div_frappe_base"
app_title = "DIV Frappe Base"
app_publisher = "Duncan Integrated Ventures LLC"
app_description = "Extensions and utilities for Frappe"
app_email = "support@duncaniv.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "div_frappe_base",
# 		"logo": "/assets/div_frappe_base/logo.png",
# 		"title": "DIV Frappe Base",
# 		"route": "/div_frappe_base",
# 		"has_permission": "div_frappe_base.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/div_frappe_base/css/div_frappe_base.css"
# uom_family.js exposes div_frappe_base.show_uom_family_dialog used by both
# the UOM list-view button and the Canonical Attribute form button.
app_include_js = ["/assets/div_frappe_base/js/uom_family.js"]

# include js, css files in header of web template
# web_include_css = "/assets/div_frappe_base/css/div_frappe_base.css"
# web_include_js = "/assets/div_frappe_base/js/div_frappe_base.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "div_frappe_base/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {
# }
doctype_list_js = {
	"UOM": "public/js/uom_list.js",
}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "div_frappe_base/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "div_frappe_base.utils.jinja_methods",
# 	"filters": "div_frappe_base.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "div_frappe_base.install.before_install"
after_install = "div_frappe_base.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "div_frappe_base.uninstall.before_uninstall"
# after_uninstall = "div_frappe_base.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "div_frappe_base.utils.before_app_install"
# after_app_install = "div_frappe_base.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "div_frappe_base.utils.before_app_uninstall"
# after_app_uninstall = "div_frappe_base.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "div_frappe_base.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"div_frappe_base.tasks.all"
# 	],
# 	"daily": [
# 		"div_frappe_base.tasks.daily"
# 	],
# 	"hourly": [
# 		"div_frappe_base.tasks.hourly"
# 	],
# 	"weekly": [
# 		"div_frappe_base.tasks.weekly"
# 	],
# 	"monthly": [
# 		"div_frappe_base.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "div_frappe_base.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "div_frappe_base.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "div_frappe_base.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "div_frappe_base.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["div_frappe_base.utils.before_request"]
# after_request = ["div_frappe_base.utils.after_request"]

# Job Events
# ----------
# before_job = ["div_frappe_base.utils.before_job"]
# after_job = ["div_frappe_base.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"div_frappe_base.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
