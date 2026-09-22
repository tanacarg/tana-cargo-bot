# ----------------------------------------------
    # OWNER
    # ----------------------------------------------

    owner_conversation = ConversationHandler(

        entry_points=[

            MessageHandler(

                filters.Regex(
                    "^📦 የጭነት ባለቤት$"
                ),

                owner_start
            )
        ],

        states={

            OWNER_NAME: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    owner_name
                )
            ],

            OWNER_PHONE: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    owner_phone
                )
            ],
        },

        fallbacks=[

            CommandHandler(
                "cancel",
                cancel
            )
        ],

        allow_reentry=True,
    )

    application.add_handler(
        owner_conversation
    )

    # ----------------------------------------------
    # SUPPORT
    # ----------------------------------------------

    support_conversation = ConversationHandler(

        entry_points=[

            MessageHandler(

                filters.Regex(
                    "^📞 Support$"
                ),

                support_start
            )
        ],

        states={

            SUPPORT_MESSAGE: [

                MessageHandler(

                    filters.TEXT
                    & ~filters.COMMAND,

                    support_message
                )
            ],
        },

        fallbacks=[

            CommandHandler(
                "cancel",
                cancel
            )
        ],

        allow_reentry=True,
    )

    application.add_handler(
        support_conversation
    )

    # ----------------------------------------------
    # CONNECTION
    # ----------------------------------------------

    application.add_handler(

        CallbackQueryHandler(

            connection_request,

            pattern=r"^connect_\d+$"
        )
    )

    application.add_handler(

        CallbackQueryHandler(

            truck_connection_request,

            pattern=r"^truckconnect_\d+$"
        )
    )

    application.add_handler(

        CallbackQueryHandler(

            accept_connection,

            pattern=r"^accept_\d+$"
        )
    )

    application.add_handler(

        CallbackQueryHandler(

            reject_connection,

            pattern=r"^reject_\d+$"
        )
    )

    # ----------------------------------------------
    # NEGOTIATION
    # ----------------------------------------------

    application.add_handler(

        CallbackQueryHandler(

            agree_price,

            pattern=(
                r"^agreeprice_\d+_\d+$"
            )
        )
    )

    application.add_handler(

        CallbackQueryHandler(

            counter_price_button,

            pattern=(
                r"^counterprice_\d+_\d+$"
            )
        )
    )

    # ----------------------------------------------
    # ADMIN PAYMENT
    # ----------------------------------------------

    application.add_handler(

        CallbackQueryHandler(

            admin_payment_action,

            pattern=(
                r"^admin(approve|reject)"
                r"_\d+_"
                r"(requester|other)"
                r"_\d+$"
            )
        )
    )

    # ----------------------------------------------
    # PHOTO RECEIPT
    # ----------------------------------------------

    application.add_handler(

        MessageHandler(

            filters.PHOTO,

            receipt_photo
        )
    )

    # ----------------------------------------------
    # ALL TEXT
    # ----------------------------------------------

    application.add_handler(

        MessageHandler(

            filters.TEXT
            & ~filters.COMMAND,

            text_router
        )
    )

    print(
        "TANA CARGO Bot is starting..."
    )
