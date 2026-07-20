// Copyright (c) 2026, Duncan Integrated Ventures LLC and contributors
// For license information, please see license.txt

// Reusable label photo capture with a banking-app-style quality gate + auto-snap.
//
// `div_frappe_base.label_capture.open_camera(opts)` opens a live-preview popup,
// runs a lightweight per-frame analysis in the browser — focus (Laplacian
// variance), exposure + glare (luma histogram), steadiness (frame diff), and a
// barcode-visible signal where the native BarcodeDetector exists — shows a
// Good/Poor readout, and auto-captures once quality holds for a short streak.
// The snapped still is POSTed to `opts.extract_endpoint` (default the generic
// div_frappe_base extractor) and the parsed result is handed to `opts.on_extract`.
//
// Falls back to a native file/camera input when getUserMedia is unavailable or
// denied (older iPad Safari, insecure origin).
//
// opts:
//   on_extract(data)   required — called with the endpoint's response
//   extract_endpoint   optional — dotted path (default the generic extractor)
//   extra_args         optional — object merged into the POST (supplier, parent…)
//   engine, layout     optional — forwarded to the endpoint
//   title              optional — popup title

frappe.provide('div_frappe_base.label_capture')

div_frappe_base.label_capture.open_camera = function (opts) {
	opts = opts || {}
	const endpoint = opts.extract_endpoint || 'div_frappe_base.label_capture.api.extract_label'

	// Quality thresholds — conservative; revisit against real device photos.
	const ANALYZE_W = 240 // downscale width for per-frame analysis
	const INTERVAL_MS = 150
	const FOCUS_MIN = 8 // Laplacian variance floor (blur below this)
	const LUMA_MIN = 50
	const LUMA_MAX = 215
	const GLARE_MAX = 0.06 // max fraction of blown-out pixels
	const STEADY_MAX = 6 // max mean abs luma diff between frames
	const GOOD_STREAK = 5 // consecutive good frames before auto-snap (~0.75s)

	let stream = null
	let timer = null
	let prevGray = null
	let goodStreak = 0
	let captured = false

	const popup = new frappe.ui.Dialog({
		title: opts.title || __('Capture Label Photo'),
		size: 'large',
		fields: [{ fieldname: 'cam', fieldtype: 'HTML' }],
		primary_action_label: __('Capture Now'),
		primary_action: () => capture(true),
		secondary_action_label: __('Cancel'),
		secondary_action: () => popup.hide(),
	})

	const $wrap = popup.fields_dict.cam.$wrapper
	$wrap.html(`
		<div class="label-cam" style="display:flex;flex-direction:column;gap:8px;align-items:center;">
			<video playsinline muted autoplay style="max-width:100%;border-radius:6px;background:#000;"></video>
			<div class="qchips" style="display:flex;gap:8px;flex-wrap:wrap;font-size:12px;">
				<span data-k="focus" class="qchip">Focus</span>
				<span data-k="light" class="qchip">Light</span>
				<span data-k="steady" class="qchip">Steady</span>
				<span data-k="codes" class="qchip">Codes: 0</span>
			</div>
			<div class="qstatus text-muted" style="font-size:13px;">${__('Point the camera at the label…')}</div>
			<input type="file" accept="image/*" capture="environment" class="cam-fallback" style="display:none;">
		</div>
	`)
	const video = $wrap.find('video')[0]
	const $status = $wrap.find('.qstatus')
	const $chips = $wrap.find('.qchip')
	const $fallback = $wrap.find('.cam-fallback')

	const setChip = (k, ok, label) => {
		const el = $chips.filter(`[data-k="${k}"]`)
		if (label) el.text(label)
		el.css({
			padding: '2px 8px',
			'border-radius': '10px',
			background: ok ? '#d4edda' : '#f8d7da',
			color: ok ? '#155724' : '#721c24',
		})
	}

	const work = document.createElement('canvas')
	const wctx = work.getContext('2d', { willReadFrequently: true })

	const analyze = () => {
		if (!video.videoWidth) return
		const W = ANALYZE_W
		const H = Math.round((video.videoHeight / video.videoWidth) * W) || 1
		work.width = W
		work.height = H
		wctx.drawImage(video, 0, 0, W, H)
		const data = wctx.getImageData(0, 0, W, H).data
		const n = W * H
		const gray = new Float32Array(n)
		let sum = 0
		let sat = 0
		for (let i = 0; i < n; i++) {
			const y = 0.299 * data[i * 4] + 0.587 * data[i * 4 + 1] + 0.114 * data[i * 4 + 2]
			gray[i] = y
			sum += y
			if (y >= 250) sat++
		}
		const meanLuma = sum / n
		const glare = sat / n

		// Laplacian variance (focus).
		let ls = 0
		let ls2 = 0
		let c = 0
		for (let y = 1; y < H - 1; y++) {
			for (let x = 1; x < W - 1; x++) {
				const i = y * W + x
				const lap = 4 * gray[i] - gray[i - 1] - gray[i + 1] - gray[i - W] - gray[i + W]
				ls += lap
				ls2 += lap * lap
				c++
			}
		}
		const focus = c ? ls2 / c - (ls / c) * (ls / c) : 0

		// Steadiness (mean abs diff vs previous frame).
		let steady = STEADY_MAX + 1
		if (prevGray && prevGray.length === n) {
			let diff = 0
			for (let i = 0; i < n; i++) diff += Math.abs(gray[i] - prevGray[i])
			steady = diff / n
		}
		prevGray = gray

		const focusOk = focus >= FOCUS_MIN
		const lightOk = meanLuma >= LUMA_MIN && meanLuma <= LUMA_MAX && glare <= GLARE_MAX
		const steadyOk = steady <= STEADY_MAX
		setChip('focus', focusOk)
		setChip('light', lightOk)
		setChip('steady', steadyOk)

		const good = focusOk && lightOk && steadyOk
		if (good) {
			goodStreak++
			$status.text(__('Hold steady… capturing'))
		} else {
			goodStreak = 0
			$status.text(
				!focusOk
					? __('Move closer / hold still to focus')
					: !lightOk
					? __('Adjust lighting — avoid glare and shadows')
					: __('Hold the camera steady')
			)
		}
		if (goodStreak >= GOOD_STREAK) capture(false)
	}

	const detectCodes = async () => {
		if (!('BarcodeDetector' in window) || !video.videoWidth) return
		try {
			if (!detectCodes._det) {
				detectCodes._det = new window.BarcodeDetector({
					formats: ['code_128', 'data_matrix', 'qr_code', 'ean_13', 'code_39'],
				})
			}
			const codes = await detectCodes._det.detect(video)
			setChip('codes', codes.length > 0, `Codes: ${codes.length}`)
		} catch (e) {
			// BarcodeDetector unsupported for these formats — ignore.
		}
	}

	const capture = async manual => {
		if (captured) return
		if (manual && goodStreak < GOOD_STREAK) {
			const ok = await new Promise(res => {
				frappe.confirm(
					__('Image quality looks low. Capture anyway?'),
					() => res(true),
					() => res(false)
				)
			})
			if (!ok) return
		}
		captured = true
		stop()

		const cv = document.createElement('canvas')
		cv.width = video.videoWidth
		cv.height = video.videoHeight
		cv.getContext('2d').drawImage(video, 0, 0, cv.width, cv.height)
		const dataUrl = cv.toDataURL('image/jpeg', 0.9)
		popup.hide()
		await send(dataUrl)
	}

	const send = async dataUrl => {
		frappe.dom.freeze(__('Reading label…'))
		let data
		try {
			data = await frappe.xcall(endpoint, {
				image: dataUrl,
				engine: opts.engine || null,
				layout: opts.layout || null,
				...(opts.extra_args || {}),
			})
		} catch (e) {
			frappe.dom.unfreeze()
			frappe.msgprint(__('Could not read the label: ') + (e.message || e))
			return
		}
		frappe.dom.unfreeze()
		if (typeof opts.on_extract === 'function') opts.on_extract(data)
	}

	const stop = () => {
		if (timer) {
			clearInterval(timer)
			timer = null
		}
		if (stream) {
			stream.getTracks().forEach(t => t.stop())
			stream = null
		}
	}

	const useFallback = msg => {
		stop()
		$status.text(msg || __('Live camera unavailable — use the file picker.'))
		video.style.display = 'none'
		$fallback.show()
	}

	$fallback.on('change', e => {
		const file = e.target.files && e.target.files[0]
		if (!file) return
		const reader = new FileReader()
		reader.onload = async () => {
			popup.hide()
			await send(reader.result)
		}
		reader.readAsDataURL(file)
	})

	popup.$wrapper.on('hidden.bs.modal', stop)
	popup.show()

	if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
		useFallback(__('This browser has no live camera access — pick a photo instead.'))
		return
	}
	navigator.mediaDevices
		.getUserMedia({ video: { facingMode: { ideal: 'environment' } } })
		.then(s => {
			stream = s
			video.srcObject = s
			timer = setInterval(() => {
				analyze()
				detectCodes()
			}, INTERVAL_MS)
		})
		.catch(() => useFallback(__('Camera permission denied — pick a photo instead.')))
}
