// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt

// Site-wide patches for the Geolocation field control:
//   1. On a New form with no value yet, recenter the map on the user's
//      approximate location. Browser geolocation is preferred, but only
//      used if permission is already granted (no prompt). IP geolocation
//      is the fallback. Existing docs and saved values are untouched.
//   2. Add a Leaflet geocoder search box to every map for address lookup.

;(() => {
	if (!window.frappe || !frappe.ui || !frappe.ui.form || !frappe.ui.form.ControlGeolocation) {
		return
	}

	const Geo = frappe.ui.form.ControlGeolocation
	const DEFAULT_ZOOM = 13
	const GEOCODER_CSS = 'https://unpkg.com/leaflet-control-geocoder@2.4.0/dist/Control.Geocoder.css'
	const GEOCODER_JS = 'https://unpkg.com/leaflet-control-geocoder@2.4.0/dist/Control.Geocoder.js'

	let location_promise = null
	let cached_latlng = null
	let geocoder_promise = null

	function resolve_user_location() {
		if (location_promise) return location_promise
		location_promise = (async () => {
			// Browser geolocation, but only if the user has already granted permission.
			// `permissions.query` lets us check state without triggering a prompt.
			if (navigator.permissions && navigator.geolocation) {
				try {
					const status = await navigator.permissions.query({ name: 'geolocation' })
					if (status.state === 'granted') {
						const pos = await new Promise((res, rej) =>
							navigator.geolocation.getCurrentPosition(res, rej, {
								timeout: 5000,
								// Accept a cached fix up to an hour old — for centering
								// a map we don't need a fresh GPS lock.
								maximumAge: 3600000,
							})
						)
						return [pos.coords.latitude, pos.coords.longitude]
					}
				} catch (e) {
					// fall through to IP lookup
				}
			}
			// IP geolocation fallback. ipapi.co uses the visitor's IP automatically.
			try {
				const r = await fetch('https://ipapi.co/json/', { credentials: 'omit' })
				const d = await r.json()
				if (d && d.latitude && d.longitude) {
					return [d.latitude, d.longitude]
				}
			} catch (e) {
				// give up — caller falls back to Frappe defaults
			}
			return null
		})()
		location_promise.then(latlng => {
			if (latlng) cached_latlng = latlng
		})
		return location_promise
	}

	// Pre-warm at script load so the cache is hot before any map opens.
	resolve_user_location()

	function load_geocoder() {
		if (geocoder_promise) return geocoder_promise
		geocoder_promise = new Promise((resolve, reject) => {
			if (window.L && L.Control && L.Control.Geocoder) {
				resolve()
				return
			}
			const css = document.createElement('link')
			css.rel = 'stylesheet'
			css.href = GEOCODER_CSS
			document.head.appendChild(css)

			const script = document.createElement('script')
			script.src = GEOCODER_JS
			script.onload = () => resolve()
			script.onerror = reject
			document.head.appendChild(script)
		})
		return geocoder_promise
	}

	const original_bind_leaflet_map = Geo.prototype.bind_leaflet_map
	Geo.prototype.bind_leaflet_map = function () {
		const is_new_empty = this.frm && this.frm.is_new() && !this.value

		// Warm path: cache is already populated. Patch the global defaults
		// briefly so the first (and only) setView inside the original method
		// already targets the user's location — tiles load directly for that
		// area instead of flashing India first. Restore immediately after.
		const defaults = frappe.utils && frappe.utils.map_defaults
		let saved_center = null
		let saved_zoom = null
		if (is_new_empty && cached_latlng && defaults) {
			saved_center = defaults.center
			saved_zoom = defaults.zoom
			defaults.center = cached_latlng
			defaults.zoom = DEFAULT_ZOOM
		}
		original_bind_leaflet_map.call(this)
		if (saved_center !== null) {
			defaults.center = saved_center
			defaults.zoom = saved_zoom
		}

		// Cold path: cache wasn't ready when the map was built — fall back to
		// async setView once the lookup resolves.
		if (is_new_empty && !cached_latlng) {
			resolve_user_location().then(latlng => {
				// Re-check value: bind_leaflet_data may have populated the map
				// between the time we kicked off the lookup and now.
				if (latlng && this.map && !this.value) {
					this.map.setView(latlng, DEFAULT_ZOOM)
				}
			})
		}

		load_geocoder()
			.then(() => {
				if (!this.map || !L.Control || !L.Control.Geocoder || this.geocoder_control) {
					return
				}
				this.geocoder_control = L.Control.geocoder({
					defaultMarkGeocode: false,
					position: 'topright',
					placeholder: __('Search address…'),
				})
					.on('markgeocode', e => {
						const bbox = e.geocode.bbox
						if (bbox) {
							this.map.fitBounds(bbox)
						} else {
							this.map.setView(e.geocode.center, DEFAULT_ZOOM)
						}
					})
					.addTo(this.map)
			})
			.catch(() => {
				// geocoder failed to load — map still works without search
			})
	}
})()
