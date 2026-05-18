document.addEventListener('DOMContentLoaded', function() {
    // ================= VALIDACIONES DE FORMULARIOS =================
    
    // Validación de cantidad (debe ser mayor a 0)
    const cantidadInputs = document.querySelectorAll('input[type="number"][min="0.01"]');
    cantidadInputs.forEach(input => {
        input.addEventListener('input', function() {
            if (this.value <= 0) {
                this.style.borderColor = '#EF4444';
                showTooltip(this, 'La cantidad debe ser mayor a 0');
            } else {
                this.style.borderColor = '#10B981';
                hideTooltip(this);
            }
        });
    });
    
    // Validación de formularios antes de enviar
    const forms = document.querySelectorAll('form:not([novalidate])');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            let isValid = true;
            
            // Validar campos requeridos
            const requiredFields = this.querySelectorAll('[required]');
            requiredFields.forEach(field => {
                if (!field.value.trim()) {
                    showTooltip(field, 'Este campo es obligatorio');
                    isValid = false;
                }
            });
            
            // Validar emails
            const emailFields = this.querySelectorAll('input[type="email"]');
            emailFields.forEach(field => {
                if (field.value && !isValidEmail(field.value)) {
                    showTooltip(field, 'Por favor ingrese un email válido');
                    isValid = false;
                }
            });
            
            // Validar contraseñas (si hay confirmación)
            const password = this.querySelector('input[name="password"]');
            const confirmPassword = this.querySelector('input[name="confirm_password"]');
            if (password && confirmPassword && password.value !== confirmPassword.value) {
                showTooltip(confirmPassword, 'Las contraseñas no coinciden');
                isValid = false;
            }
            
            if (!isValid) {
                e.preventDefault();
                showNotification('Por favor complete todos los campos correctamente', 'error');
                return false;
            }
            
            return true;
        });
    });
    
    // ================= MEJORAS DE EXPERIENCIA =================
    
    // Mejorar focus en formularios
    const formControls = document.querySelectorAll('input, select, textarea');
    formControls.forEach(control => {
        control.addEventListener('focus', function() {
            this.parentElement.classList.add('focused');
        });
        
        control.addEventListener('blur', function() {
            this.parentElement.classList.remove('focused');
        });
    });
    
    // Auto-ocultar mensajes flash
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.opacity = '0';
            alert.style.transition = 'opacity 0.5s ease';
            setTimeout(() => {
                if (alert.parentElement) {
                    alert.remove();
                }
            }, 500);
        }, 5000);
    });
    
    // Mejorar tablas con hover
    const tableRows = document.querySelectorAll('tbody tr');
    tableRows.forEach(row => {
        row.addEventListener('mouseenter', function() {
            this.style.backgroundColor = '#F9FAFB';
        });
        
        row.addEventListener('mouseleave', function() {
            this.style.backgroundColor = '';
        });
    });
    
    // ================= FUNCIONALIDADES ESPECÍFICAS =================
    
    // Dinámica de unidades según categoría
    const categoriaSelect = document.getElementById('categoria');
    if (categoriaSelect) {
        categoriaSelect.addEventListener('change', function() {
            const categoria = this.value;
            const unidadSelect = document.getElementById('unidad');
            
            if (!categoria) {
                unidadSelect.innerHTML = '<option value="">Seleccione unidad</option>';
                return;
            }
            
            // Obtener unidades desde la API
            fetch(`/api/factores/${encodeURIComponent(categoria)}`)
                .then(response => {
                    if (!response.ok) throw new Error('Error al cargar unidades');
                    return response.json();
                })
                .then(unidades => {
                    unidadSelect.innerHTML = '<option value="">Seleccione unidad</option>';
                    unidades.forEach(unidad => {
                        const option = document.createElement('option');
                        option.value = unidad;
                        option.textContent = unidad;
                        unidadSelect.appendChild(option);
                    });
                })
                .catch(error => {
                    console.error('Error:', error);
                    showNotification('Error al cargar unidades disponibles', 'error');
                });
        });
    }
    
    // Cálculo en tiempo real en formulario de registro
    const calcularBtn = document.querySelector('[data-calculate]');
    if (calcularBtn) {
        calcularBtn.addEventListener('click', function() {
            const cantidad = parseFloat(document.getElementById('cantidad').value) || 0;
            const factor = parseFloat(document.getElementById('factor').value) || 0;
            const resultado = cantidad * factor;
            
            const resultadoElement = document.getElementById('resultado');
            if (resultadoElement) {
                resultadoElement.textContent = resultado.toFixed(2);
                resultadoElement.parentElement.style.display = 'block';
            }
        });
    }
    
    // Habilitar/deshabilitar campos de proveedor en configuración
    const energeticoCheckboxes = document.querySelectorAll('input[name^="energeticos"]');
    energeticoCheckboxes.forEach(checkbox => {
        const energetico = checkbox.value;
        const proveedorInput = document.querySelector(`input[name="proveedor_${energetico}"]`);
        
        if (proveedorInput) {
            checkbox.addEventListener('change', function() {
                proveedorInput.disabled = !this.checked;
                if (!this.checked) {
                    proveedorInput.value = '';
                }
            });
            
            // Estado inicial
            proveedorInput.disabled = !checkbox.checked;
        }
    });
    
    // ================= FUNCIONES AUXILIARES =================
    
    function isValidEmail(email) {
        const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        return regex.test(email);
    }
    
    function showTooltip(element, message) {
        let tooltip = element.nextElementSibling;
        if (!tooltip || !tooltip.classList.contains('tooltip')) {
            tooltip = document.createElement('div');
            tooltip.className = 'tooltip';
            element.parentNode.insertBefore(tooltip, element.nextSibling);
        }
        tooltip.textContent = message;
        tooltip.style.display = 'block';
    }
    
    function hideTooltip(element) {
        const tooltip = element.nextElementSibling;
        if (tooltip && tooltip.classList.contains('tooltip')) {
            tooltip.style.display = 'none';
        }
    }
    
    function showNotification(message, type = 'info') {
        // Crear notificación
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.innerHTML = `
            <i class="fas fa-${type === 'error' ? 'exclamation-circle' : 'info-circle'}"></i>
            <span>${message}</span>
            <button onclick="this.parentElement.remove()">×</button>
        `;
        
        // Estilos para notificación
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 1rem 1.5rem;
            background: ${type === 'error' ? '#FEE2E2' : type === 'success' ? '#D1FAE5' : '#E0F2FE'};
            color: ${type === 'error' ? '#991B1B' : type === 'success' ? '#065F46' : '#1E40AF'};
            border-radius: 8px;
            display: flex;
            align-items: center;
            gap: 10px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            z-index: 9999;
            animation: slideIn 0.3s ease;
        `;
        
        document.body.appendChild(notification);
        
        // Auto-remover después de 5 segundos
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease';
            setTimeout(() => notification.remove(), 300);
        }, 5000);
    }
    
    // Agregar estilos para animaciones
    const style = document.createElement('style');
    style.textContent = `
        @keyframes slideIn {
            from {
                transform: translateX(100%);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }
        
        @keyframes slideOut {
            from {
                transform: translateX(0);
                opacity: 1;
            }
            to {
                transform: translateX(100%);
                opacity: 0;
            }
        }
        
        .tooltip {
            position: absolute;
            background: #EF4444;
            color: white;
            padding: 0.5rem 1rem;
            border-radius: 4px;
            font-size: 0.875rem;
            margin-top: 5px;
            z-index: 100;
            display: none;
        }
        
        .tooltip:before {
            content: '';
            position: absolute;
            top: -5px;
            left: 10px;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-bottom: 5px solid #EF4444;
        }
        
        .notification button {
            background: none;
            border: none;
            font-size: 1.2rem;
            cursor: pointer;
            color: inherit;
            margin-left: 10px;
        }
        
        .form-group.focused .form-label {
            color: #10B981;
        }
        
        .form-group.focused .form-control {
            border-color: #10B981;
            box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.1);
        }
    `;
    document.head.appendChild(style);
    
    // ================= MEJORAS DE ACCESIBILIDAD =================
    
    // Mejorar navegación por teclado
    document.addEventListener('keydown', function(e) {
        // Atajos de teclado
        if (e.ctrlKey && e.key === 'Enter') {
            const submitBtn = document.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.click();
            }
        }
        
        // Navegación por formularios con Tab
        if (e.key === 'Tab') {
            const focused = document.activeElement;
            if (focused && focused.classList.contains('form-control')) {
                focused.parentElement.classList.add('focused');
            }
        }
    });
    
    // Focus en primer campo de formularios
    const mainForm = document.querySelector('main form');
    if (mainForm) {
        const firstInput = mainForm.querySelector('input, select, textarea');
        if (firstInput && !firstInput.value) {
            setTimeout(() => firstInput.focus(), 100);
        }
    }
    
    // ================= INICIALIZACIÓN DE COMPONENTES =================
    
    console.log('EcoTrack JavaScript cargado correctamente');
});