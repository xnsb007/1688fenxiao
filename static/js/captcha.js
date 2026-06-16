/**
 * AJ-Captcha 滑动验证码组件
 * 参考文档: https://ajcaptcha.beliefteam.cn/captcha-doc/captchaDoc/html.html
 */

(function(global) {
    'use strict';

    function Captcha(options) {
        this.options = Object.assign({
            baseUrl: '/api/captcha',
            mode: 'pop',
            containerId: '',
            vSpace: 5,
            explain: '向右滑动完成验证',
            imgSize: { width: '310px', height: '155px' },
            success: null,
            error: null,
            ready: null,
            beforeCheck: null
        }, options);

        this.token = '';
        this.secretKey = '';
        this.sliderWidth = 47;
        this.isVerifying = false;
        this.container = null;
    }

    Captcha.prototype.init = function() {
        if (this.options.mode === 'fixed' && this.options.containerId) {
            this.container = document.getElementById(this.options.containerId);
            if (this.container) {
                this.createUI();
                this.fetchCaptcha();
            }
        }
        
        if (this.options.ready && typeof this.options.ready === 'function') {
            this.options.ready();
        }
        
        return this;
    };

    Captcha.prototype.show = function() {
        if (this.options.mode === 'pop') {
            this.createModal();
        } else if (this.container) {
            this.refresh();
        }
    };

    Captcha.prototype.createModal = function() {
        const existingModal = document.getElementById('aj-captcha-modal');
        if (existingModal) {
            existingModal.remove();
        }

        const modal = document.createElement('div');
        modal.id = 'aj-captcha-modal';
        modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:9999;display:flex;justify-content:center;align-items:center;';

        const content = document.createElement('div');
        content.style.cssText = 'background:#fff;border-radius:8px;padding:20px;box-shadow:0 4px 20px rgba(0,0,0,0.3);position:relative;';

        const header = document.createElement('div');
        header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:15px;';
        header.innerHTML = '<span style="font-size:16px;font-weight:500;color:#333;">请完成安全验证</span><button id="aj-captcha-close" style="background:none;border:none;font-size:20px;cursor:pointer;color:#999;line-height:1;">&times;</button>';

        const container = document.createElement('div');
        container.id = 'aj-captcha-container';

        content.appendChild(header);
        content.appendChild(container);
        modal.appendChild(content);
        document.body.appendChild(modal);

        document.getElementById('aj-captcha-close').addEventListener('click', () => this.closeModal());
        modal.addEventListener('click', (e) => { if (e.target === modal) this.closeModal(); });

        this.container = container;
        this.modal = modal;
        this.createUI();
        this.fetchCaptcha();
    };

    Captcha.prototype.closeModal = function() {
        if (this.modal) {
            this.modal.remove();
            this.modal = null;
        }
    };

    Captcha.prototype.createUI = function() {
        if (!this.container) return;

        const width = this.options.imgSize.width || '310px';
        const height = this.options.imgSize.height || '155px';
        const vSpace = this.options.vSpace || 5;

        this.container.innerHTML = '<div class="aj-captcha-wrapper" style="width:' + width + ';user-select:none;-webkit-user-select:none;"><div class="aj-captcha-image" style="position:relative;width:' + width + ';height:' + height + ';background:#f5f5f5;border-radius:4px;overflow:hidden;"><img class="aj-captcha-bg" style="width:100%;height:100%;object-fit:cover;display:none;"/><div class="aj-captcha-slider" style="position:absolute;left:0;top:0;width:47px;height:100%;background-size:100% 100%;background-position:left top;background-repeat:no-repeat;display:none;box-shadow:none;"></div><div class="aj-captcha-loading" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#999;font-size:14px;">加载中...</div></div><div class="aj-captcha-control" style="margin-top:' + vSpace + 'px;height:40px;background:#f7f9fa;border-radius:20px;position:relative;border:1px solid #e4e7eb;"><div class="aj-captcha-track" style="position:absolute;left:0;top:0;height:100%;width:0;background:#d1e9ff;border-radius:20px;"></div><div class="aj-captcha-btn" style="position:absolute;left:0;top:0;width:40px;height:38px;background:#fff;border-radius:50%;box-shadow:0 2px 6px rgba(0,0,0,0.2);cursor:pointer;display:flex;align-items:center;justify-content:center;border:1px solid #ddd;"><svg viewBox="0 0 1024 1024" style="width:20px;height:20px;fill:#666;"><path d="M384 512L731.733333 202.666667c17.066667-14.933333 19.2-42.666667 4.266667-59.733334-14.933333-17.066667-42.666667-19.2-59.733333-4.266666l-384 341.333333c-10.666667 8.533333-14.933333 19.2-14.933334 32s4.266667 23.466667 14.933334 32l384 341.333333c8.533333 6.4 19.2 10.666667 29.866666 10.666667 10.666667 0 23.466667-4.266667 32-12.8 14.933333-17.066667 14.933333-44.8-4.266666-59.733333L384 512z"></path></svg></div><div class="aj-captcha-text" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:#999;font-size:14px;pointer-events:none;">' + this.options.explain + '</div></div></div>';

        this.bindEvents();
    };

    Captcha.prototype.bindEvents = function() {
        if (!this.container) return;

        const btn = this.container.querySelector('.aj-captcha-btn');
        const control = this.container.querySelector('.aj-captcha-control');

        let isDragging = false;
        let startX = 0;
        let currentX = 0;

        const onStart = (e) => {
            if (this.isVerifying) return;
            isDragging = true;
            startX = e.type.includes('mouse') ? e.clientX : e.touches[0].clientX;
            btn.style.transition = 'none';
            this.container.querySelector('.aj-captcha-track').style.transition = 'none';
        };

        const onMove = (e) => {
            if (!isDragging) return;
            const x = e.type.includes('mouse') ? e.clientX : e.touches[0].clientX;
            const maxX = Math.max(0, control.offsetWidth - 40);
            currentX = Math.max(0, Math.min(x - startX, maxX));

            btn.style.left = currentX + 'px';
            this.container.querySelector('.aj-captcha-track').style.width = (currentX + 20) + 'px';

            const slider = this.container.querySelector('.aj-captcha-slider');
            if (slider) {
                const imgWidth = this.container.querySelector('.aj-captcha-image').offsetWidth;
                const sliderMaxX = Math.max(0, imgWidth - slider.offsetWidth);
                const ratio = maxX > 0 ? (currentX / maxX) : 0;
                const sliderX = ratio * sliderMaxX;
                slider.style.left = sliderX + 'px';
            }
        };

        const onEnd = () => {
            if (!isDragging) return;
            isDragging = false;
            const maxX = Math.max(0, control.offsetWidth - 40);
            if (currentX <= 0) {
                this.reset();
                return;
            }
            this.verify(currentX, control.offsetWidth);
        };

        btn.addEventListener('mousedown', onStart);
        btn.addEventListener('touchstart', onStart);
        document.addEventListener('mousemove', onMove);
        document.addEventListener('touchmove', onMove);
        document.addEventListener('mouseup', onEnd);
        document.addEventListener('touchend', onEnd);
        document.addEventListener('touchcancel', onEnd);
    };

    Captcha.prototype.getImageSrc = function(value) {
        if (!value) return '';
        if (value.startsWith('data:image') || value.startsWith('http://') || value.startsWith('https://') || value.startsWith('/')) {
            return value;
        }
        return 'data:image/png;base64,' + value;
    };

    Captcha.prototype.fetchCaptcha = function() {
        const url = this.options.baseUrl + '/get';
        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ captchaType: 'blockPuzzle' })
        })
        .then(r => r.json())
        .then(data => {
            if (data.repCode === '0000' || data.success) {
                this.token = data.repData?.token || data.token;
                this.secretKey = data.repData?.secretKey || data.secretKey;
                const bg = this.container.querySelector('.aj-captcha-bg');
                const slider = this.container.querySelector('.aj-captcha-slider');
                const bgImageSrc = this.getImageSrc(data.repData?.originalImageBase64 || data.originalImageBase64);
                const sliderImageSrc = this.getImageSrc(data.repData?.jigsawImageBase64 || data.jigsawImageBase64);
                this.sliderWidth = 47;
                const sliderImage = new Image();
                sliderImage.onload = () => {
                    if (sliderImage.naturalWidth > 0) {
                        this.sliderWidth = sliderImage.naturalWidth;
                        slider.style.width = this.sliderWidth + 'px';
                    }
                    if (sliderImage.naturalHeight > 0) {
                        slider.style.height = sliderImage.naturalHeight + 'px';
                    }
                };
                sliderImage.src = sliderImageSrc;
                bg.src = bgImageSrc;
                slider.style.backgroundImage = 'url("' + sliderImageSrc + '")';
                bg.style.display = 'block';
                slider.style.display = 'block';
                this.container.querySelector('.aj-captcha-loading').style.display = 'none';
            } else {
                this.showError('获取验证码失败');
            }
        })
        .catch(err => {
            console.error('Fetch captcha error:', err);
            this.showError('网络错误');
        });
    };

    Captcha.prototype.verify = function(moveBlockLeft, barWidth) {
        if (this.isVerifying) return;
        this.isVerifying = true;

        const btn = this.container.querySelector('.aj-captcha-btn');
        btn.innerHTML = '<svg viewBox="0 0 1024 1024" style="width:20px;height:20px;fill:#52c41a;"><path d="M912 192l-64-64-352 352-160-160-64 64 224 224z"/></svg>';

        const url = this.options.baseUrl + '/check';
        const moveLeftDistance = (Number(moveBlockLeft || 0) * 310) / Math.max(1, Number(barWidth || 310));
        const checkPoint = JSON.stringify({ x: moveLeftDistance, y: 5.0 });
        const pointJson = this.encrypt(checkPoint);
        const captchaVerification = this.encrypt(this.token + '---' + checkPoint);

        fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                captchaType: 'blockPuzzle',
                token: this.token,
                pointJson: pointJson
            })
        })
        .then(r => r.json())
        .then(data => {
            const isSuccess = data.repCode === '0000';

            if (isSuccess) {
                this.showSuccess();
                if (this.options.success) {
                    this.options.success({
                        token: this.token,
                        pointJson: pointJson,
                        captchaVerification: captchaVerification
                    });
                }
            } else {
                this.showError(data.repMsg || '验证失败');
                setTimeout(() => this.refresh(), 1000);
            }
        })
        .catch(err => {
            console.error('Verify error:', err);
            this.showError('验证失败');
            setTimeout(() => this.refresh(), 1000);
        });
    };

    Captcha.prototype.encrypt = function(text) {
        if (!this.secretKey || typeof CryptoJS === 'undefined') return text;
        try {
            const key = CryptoJS.enc.Utf8.parse(this.secretKey);
            const data = CryptoJS.enc.Utf8.parse(text);
            return CryptoJS.AES.encrypt(data, key, { mode: CryptoJS.mode.ECB, padding: CryptoJS.pad.Pkcs7 }).toString();
        } catch (e) {
            return text;
        }
    };

    Captcha.prototype.reset = function() {
        const btn = this.container.querySelector('.aj-captcha-btn');
        const track = this.container.querySelector('.aj-captcha-track');
        const slider = this.container.querySelector('.aj-captcha-slider');
        btn.style.transition = 'left 0.3s';
        track.style.transition = 'width 0.3s';
        btn.style.left = '0px';
        track.style.width = '0px';
        if (slider) slider.style.left = '0px';
    };

    Captcha.prototype.refresh = function() {
        this.isVerifying = false;
        this.reset();
        this.container.querySelector('.aj-captcha-btn').innerHTML = '<svg viewBox="0 0 1024 1024" style="width:20px;height:20px;fill:#666;"><path d="M384 512L731.733333 202.666667c17.066667-14.933333 19.2-42.666667 4.266667-59.733334-14.933333-17.066667-42.666667-19.2-59.733333-4.266666l-384 341.333333c-10.666667 8.533333-14.933333 19.2-14.933334 32s4.266667 23.466667 14.933334 32l384 341.333333c8.533333 6.4 19.2 10.666667 29.866666 10.666667 10.666667 0 23.466667-4.266667 32-12.8 14.933333-17.066667 14.933333-44.8-4.266666-59.733333L384 512z"></path></svg>';
        this.fetchCaptcha();
    };

    Captcha.prototype.showSuccess = function() {
        const btn = this.container.querySelector('.aj-captcha-btn');
        const track = this.container.querySelector('.aj-captcha-track');
        const text = this.container.querySelector('.aj-captcha-text');
        btn.innerHTML = '<svg viewBox="0 0 1024 1024" style="width:20px;height:20px;fill:#fff;"><path d="M912 192l-64-64-352 352-160-160-64 64 224 224z"/></svg>';
        btn.style.background = '#52c41a';
        btn.style.borderColor = '#52c41a';
        track.style.background = '#52c41a';
        text.textContent = '验证成功';
        text.style.color = '#fff';
    };

    Captcha.prototype.showError = function(msg) {
        const text = this.container.querySelector('.aj-captcha-text');
        text.textContent = msg;
        text.style.color = '#ff4d4f';
        if (this.options.error) this.options.error(msg);
    };

    // 静态方法
    Captcha.init = function(options) {
        const instance = new Captcha(options);
        return instance.init();
    };

    Captcha.show = function() {
        if (window.captchaInstance) {
            window.captchaInstance.show();
        }
    };

    global.Captcha = Captcha;

})(window);
