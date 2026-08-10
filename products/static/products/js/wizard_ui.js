/* ==========================================================
 * Wizard UI
 * Toast + Confirm Modal + Loading
 * ========================================================== */


const WizardUI = {


    /* ------------------------------------------------------
     * Config
     * ------------------------------------------------------ */


    MAX_TOASTS: 3,

    confirmOpen: false,



    /* ------------------------------------------------------
     * Toast
     * ------------------------------------------------------ */


    showToast(
        type,
        message,
        duration = 3000
    ) {


        const allowedTypes = [
            "success",
            "error",
            "warning",
            "info",
        ];


        if (
            !allowedTypes.includes(type)
        ) {

            type = "info";

        }



        const container =
            document.getElementById(
                "wizard-toast-container"
            );



        if (!container) {

            return;

        }



        const existingToasts =
            container.querySelectorAll(
                ".wizard-toast:not(.wizard-toast-loading)"
            );



        if (
            existingToasts.length >= this.MAX_TOASTS
        ) {


            const oldest =
                existingToasts[0];


            clearTimeout(
                oldest._timer
            );


            clearTimeout(
                oldest._removeTimer
            );


            oldest.remove();

        }



        const toast =
            document.createElement(
                "div"
            );



        toast.className =
            `wizard-toast wizard-toast-${type}`;



        const icon =
            document.createElement(
                "i"
            );



        if (type === "success") {


            icon.className =
                "fa-solid fa-circle-check";


        }
        else if (type === "error") {


            icon.className =
                "fa-solid fa-circle-exclamation";


        }
        else if (type === "warning") {


            icon.className =
                "fa-solid fa-triangle-exclamation";


        }
        else {


            icon.className =
                "fa-solid fa-circle-info";


        }



        const text =
            document.createElement(
                "div"
            );


        text.textContent =
            message;



        const closeButton =
            document.createElement(
                "button"
            );


        closeButton.type =
            "button";


        closeButton.className =
            "wizard-toast-close";


        closeButton.setAttribute(
            "aria-label",
            "Kapat"
        );


        closeButton.innerHTML =
            "&times;";



        toast.appendChild(icon);

        toast.appendChild(text);

        toast.appendChild(closeButton);



        container.appendChild(
            toast
        );



        function removeToast() {


            clearTimeout(
                toast._timer
            );


            clearTimeout(
                toast._removeTimer
            );



            toast.classList.remove(
                "show"
            );



            toast._removeTimer =
                setTimeout(
                    () => {

                        toast.remove();

                    },
                    300
                );

        }

        toast.close = removeToast;



        requestAnimationFrame(
            () => {

                toast.classList.add(
                    "show"
                );

            }
        );



        toast._timer =
            setTimeout(
                removeToast,
                duration
            );



        closeButton.addEventListener(
            "click",
            removeToast
        );


        return toast;

    },




    /* ------------------------------------------------------
     * Loading Toast
     * ------------------------------------------------------ */


    showLoading(
        message = "Yükleniyor..."
    ) {
    
        const toast = this.showToast(
            "info",
            message,
            999999
        );
    
    
        if (toast) {
        
            toast.classList.add(
                "wizard-toast-loading"
            );
        
        }
    
    
        return toast;
    
    },



    /* ------------------------------------------------------
     * Button Loading
     * ------------------------------------------------------ */

    setButtonLoading(
        button,
        loading,
        text = "Yükleniyor..."
    ) {

        if (!button) {
            return;
        }


        if (loading) {

            if (!button.dataset.originalHtml) {

                button.dataset.originalHtml =
                    button.innerHTML;

            }


            button.disabled = true;


            button.innerHTML = `
                <i class="fa-solid fa-spinner fa-spin"></i>
                ${text}
            `;

        }
        else {

            button.disabled = false;


            if (button.dataset.originalHtml) {

                button.innerHTML =
                    button.dataset.originalHtml;

                delete button.dataset.originalHtml;

            }

        }

    },




    /* ------------------------------------------------------
     * Confirm Modal
     * ------------------------------------------------------ */


    showConfirm({

        title = "Emin misiniz?",

        message =
            "Bu işlem geri alınamaz.",

        confirmText = "Onayla",

        cancelText = "Vazgeç",

    } = {}) {



        if (this.confirmOpen) {

            return new Promise(
                resolve => {
                
                    setTimeout(
                        ()=>resolve(false),
                        0
                    );
                
                }
            );

        }



        this.confirmOpen = true;



        return new Promise(
            resolve => {


                const modal =
                    document.getElementById(
                        "wizard-confirm-modal"
                    );



                if (!modal) {


                    this.confirmOpen = false;


                    resolve(
                        window.confirm(
                            message
                        )
                    );


                    return;

                }




                const titleElement =
                    modal.querySelector(
                        "[data-modal-title]"
                    );


                const messageElement =
                    modal.querySelector(
                        "[data-modal-message]"
                    );


                const confirmButton =
                    modal.querySelector(
                        "[data-modal-confirm]"
                    );


                const cancelButton =
                    modal.querySelector(
                        "[data-modal-cancel]"
                    );




                if (
                    !titleElement ||
                    !messageElement ||
                    !confirmButton ||
                    !cancelButton
                ) {


                    this.confirmOpen = false;


                    resolve(
                        window.confirm(
                            message
                        )
                    );


                    return;

                }



                const previousFocus =
                    document.activeElement;



                titleElement.textContent =
                    title;


                messageElement.textContent =
                    message;


                confirmButton.textContent =
                    confirmText;


                cancelButton.textContent =
                    cancelText;



                modal.hidden =
                    false;



                document.body.classList.add(
                    "modal-open"
                );




                function close(result) {



                    modal.hidden =
                        true;



                    document.body.classList.remove(
                        "modal-open"
                    );



                    WizardUI.confirmOpen = false;



                    document.removeEventListener(
                        "keydown",
                        keyHandler
                    );


                    modal.removeEventListener(
                        "click",
                        backgroundHandler
                    );


                    confirmButton.removeEventListener(
                        "click",
                        confirmHandler
                    );


                    cancelButton.removeEventListener(
                        "click",
                        cancelHandler
                    );



                    if (
                        previousFocus &&
                        previousFocus.focus
                    ) {

                        previousFocus.focus();

                    }



                    resolve(
                        result
                    );

                }



                const boundClose =
                    close.bind(this);




                function confirmHandler() {

                    boundClose(true);

                }



                function cancelHandler() {

                    boundClose(false);

                }



                function keyHandler(event) {


                    if (
                        event.key === "Escape"
                    ) {

                        boundClose(false);

                    }

                }



                function backgroundHandler(event) {


                    if (
                        event.target === modal
                    ) {

                        boundClose(false);

                    }

                }



                confirmButton.addEventListener(
                    "click",
                    confirmHandler
                );


                cancelButton.addEventListener(
                    "click",
                    cancelHandler
                );



                document.addEventListener(
                    "keydown",
                    keyHandler
                );



                modal.addEventListener(
                    "click",
                    backgroundHandler
                );



                requestAnimationFrame(
                    () => {

                        confirmButton.focus();

                    }
                );


            }
        );

    },


};