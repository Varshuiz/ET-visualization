
"""
URL configuration for et_site project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # ET Calculator app URLs
    path('et/', include('et.urls')),
    
    # Redirect root to ET comparison calculator (main page)
    path('', RedirectView.as_view(url='/et/fetch-data/', permanent=False)),
    
    # Alternative direct access routes
    path('calculator/', include('et.urls')),  # Alternative path
    path('evapotranspiration/', include('et.urls')),  # SEO-friendly path
    
]

# Optional: Add custom error handlers
# handler404 = 'et.views.custom_404_view'
# handler500 = 'et.views.custom_500_view'
