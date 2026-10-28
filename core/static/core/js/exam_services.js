(function () {
    'use strict';
    const root = document.getElementById('exam-services-editor');
    const data = document.getElementById('exam-service-catalog-data');
    if (!root || !data) return;
    const catalog = JSON.parse(data.textContent);
    const byId = new Map(catalog.map(item => [String(item.id), item]));
    const rows = document.getElementById('performed-services-rows');
    const total = document.getElementById('id_services-TOTAL_FORMS');
    const template = document.getElementById('service-empty-row');
    const message = document.getElementById('service-editor-message');
    const search = document.getElementById('service-search');
    const results = document.getElementById('service-search-results');
    const price = value => String(value).replace(/\B(?=(\d{3})+(?!\d))/g, '.') + ' VND';
    const normalize = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[đĐ]/g, 'd').toLowerCase();
    const deleted = row => row.querySelector('[name$="-DELETE"]').checked;

    function updateTotals() {
        let totalCost = 0n;
        let invalid = false;
        Array.from(rows.children).forEach(row => {
            if (deleted(row)) return;
            const item = byId.get(row.querySelector('select').value);
            const quantity = row.querySelector('[name$="-so_luong"]').value;
            const output = row.querySelector('.service-line-total');
            if (!item) { output.textContent = '—'; return; }
            if (!/^[0-9]+$/.test(quantity) || BigInt(quantity) < 1n || BigInt(quantity) > 2147483647n) {
                invalid = true;
                output.textContent = 'Kiểm tra số lượng';
                return;
            }
            const amount = BigInt(item.price) * BigInt(quantity);
            totalCost += amount;
            output.textContent = price(amount);
        });
        document.getElementById('service-estimated-total').textContent = invalid ? 'Kiểm tra số lượng' : price(totalCost);
    }

    function updatePrice(row) {
        const item = byId.get(row.querySelector('select').value);
        row.querySelector('.service-unit-price').textContent = item
            ? price(item.price) + (item.active ? '' : ' · Ngừng áp dụng') : '—';
        updateTotals();
    }

    function wireRow(row) {
        const checkbox = row.querySelector('[name$="-DELETE"]');
        function hideDeletedRow() {
            row.hidden = checkbox.checked;
            // Deleted rows must not fail browser quantity validation while hidden.
            row.querySelectorAll('select, textarea, input:not([type="checkbox"])').forEach(field => {
                field.disabled = checkbox.checked;
            });
        }
        hideDeletedRow();
        row.querySelector('.service-delete-label').hidden = true;
        const remove = row.querySelector('.service-remove');
        remove.hidden = false;
        remove.addEventListener('click', () => { checkbox.checked = true; hideDeletedRow(); updateTotals(); });
        row.querySelector('select').addEventListener('change', () => updatePrice(row));
        row.querySelector('[name$="-so_luong"]').addEventListener('input', updateTotals);
        updatePrice(row);
    }

    function addRow() {
        // Only substitute our own integer index into the server-rendered template.
        const index = Number(total.value);
        const holder = document.createElement('tbody');
        holder.innerHTML = template.innerHTML.replace(/__prefix__/g, String(index));
        const row = holder.firstElementChild;
        rows.appendChild(row);
        total.value = String(index + 1);
        wireRow(row);
        return row;
    }

    function choose(item) {
        const current = Array.from(rows.children).filter(row => !deleted(row));
        const duplicate = current.find(row => row.querySelector('select').value === String(item.id));
        if (duplicate) {
            message.textContent = 'Dịch vụ đã có trong phiếu. Hãy sửa số lượng trên dòng đã chọn.';
            duplicate.querySelector('[name$="-so_luong"]').focus();
            return;
        }
        const row = current.find(row => !row.querySelector('select').value) || addRow();
        row.querySelector('select').value = String(item.id);
        row.querySelector('[name$="-so_luong"]').value = '1';
        updatePrice(row);
        message.textContent = 'Đã chọn ' + item.name + '. Lưu phiếu để ghi nhận thay đổi.';
    }

    function searchServices() {
        const keyword = normalize(search.value.trim());
        const matches = catalog.filter(item => item.active && normalize(item.code + ' ' + item.name).includes(keyword));
        results.replaceChildren();
        matches.slice(0, 20).forEach(item => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn ghost';
            button.textContent = item.code + ' · ' + item.name + ' · ' + price(item.price) + (item.consultation ? ' · Phí khám' : '');
            button.addEventListener('click', () => choose(item));
            results.appendChild(button);
        });
        document.getElementById('service-search-summary').textContent = matches.length
            ? matches.length + ' dịch vụ phù hợp' + (matches.length > 20 ? ' — hiển thị 20, nhập thêm để thu hẹp.' : '.')
            : 'Không có dịch vụ đang áp dụng phù hợp. Liên hệ Admin nếu danh mục chưa được cấu hình.';
    }

    Array.from(rows.children).forEach(wireRow);
    document.getElementById('service-search-panel').hidden = false;
    const add = document.getElementById('service-add-row');
    add.hidden = false;
    add.addEventListener('click', () => addRow().querySelector('select').focus());
    search.addEventListener('input', searchServices);
    search.addEventListener('keydown', event => {
        if (event.key === 'Enter') event.preventDefault();
    });
    searchServices();
})();
